"""Offline, fail-closed capture-offset measurements. Never controls Live."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import struct
import wave

import numpy as np
import soundfile as sf
from scipy.signal import fftconvolve


_RATES = {32000, 44100, 48000, 88200, 96000}
_MAX_BYTES = 32 * 1024 * 1024
_MIN_CORRELATION = 0.90
_MAX_SECONDARY = 0.70


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: dict) -> bytes:
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"),
                            allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("Evidence/configuration must be finite JSON") from exc
    if len(result) > 128 * 1024:
        raise ValueError("Evidence/configuration exceeds 128 KiB")
    return result


def _path(value: str) -> Path:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("A nonempty filesystem path is required")
    return Path(value).absolute()


def _probe_bytes(sample_rate: int, channels: int, seed: int) -> tuple[bytes, list[dict]]:
    if type(sample_rate) is not int or sample_rate not in _RATES:
        raise ValueError("Unsupported sample rate; use 32000/44100/48000/88200/96000")
    if type(channels) is not int or channels not in (1, 2):
        raise ValueError("channels must be 1 or 2")
    if type(seed) is not int or not 0 <= seed <= 0xFFFFFFFF:
        raise ValueError("seed must be an integer in 0..4294967295")
    count = sample_rate // 12
    landmarks = [{"start_sample": sample_rate * ms // 1000, "sample_count": count}
                 for ms in (500, 1370, 2610, 3890)]
    audio = np.zeros((sample_rate * 5, channels), dtype="<i2")
    for i, event in enumerate(landmarks):
        for channel in range(channels):
            key = f"film-calibration-probe/v1:{seed}:{sample_rate}:{channel}:{i}".encode()
            raw = hashlib.shake_256(key).digest(count * 2)
            signal = np.frombuffer(raw, dtype="<u2").astype(np.float64) / 32768 - 1
            signal = (signal - signal.mean()) * np.hanning(count)
            signal *= 8192 / np.max(np.abs(signal))
            start = event["start_sample"]
            audio[start:start + count, channel] = np.rint(signal).astype("<i2")
    target = io.BytesIO()
    with wave.open(target, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(audio.tobytes())
    return target.getvalue(), landmarks


def generate_calibration_probe(output_path: str, sample_rate: int = 48000,
                               channels: int = 2, seed: int = 0) -> dict:
    """Create deterministic 5s PCM16 WAV exclusively; never overwrite/create parents."""
    payload, landmarks = _probe_bytes(sample_rate, channels, seed)
    path = _path(output_path)
    # Exclusive open also refuses existing symlinks. Failure never removes user files.
    with path.open("xb") as stream:
        stream.write(payload)
    return {"schema": "film-calibration-probe/v1", "path": str(path),
            "sha256": _digest(payload), "sample_rate": sample_rate, "channels": channels,
            "seed": seed, "sample_count": sample_rate * 5, "landmarks": landmarks}


def _check_riff(payload: bytes) -> int:
    if (len(payload) < 12 or payload[:4] != b"RIFF" or payload[8:12] != b"WAVE"
            or struct.unpack_from("<I", payload, 4)[0] != len(payload) - 8):
        raise ValueError("Truncated or unsupported RIFF WAV container")
    position, required, data_size = 12, [], 0
    while position < len(payload):
        if position + 8 > len(payload):
            raise ValueError("Truncated RIFF chunk header")
        name = payload[position:position + 4]
        size = struct.unpack_from("<I", payload, position + 4)[0]
        position += 8 + size + size % 2
        if position > len(payload):
            raise ValueError("Truncated RIFF chunk data")
        if name in (b"fmt ", b"data"):
            required.append(name)
        if name == b"data":
            data_size = size
    if required != [b"fmt ", b"data"]:
        raise ValueError("RIFF requires one format followed by one data chunk")
    return data_size


def _read_wav(path: str) -> tuple[np.ndarray, int, str]:
    with _path(path).open("rb") as stream:
        payload = stream.read(_MAX_BYTES + 1)
    if len(payload) > _MAX_BYTES:
        raise ValueError("WAV exceeds 32 MiB")
    data_size = _check_riff(payload)
    try:
        with sf.SoundFile(io.BytesIO(payload)) as wav:
            if (wav.format != "WAV" or wav.subtype not in {"PCM_16", "PCM_24", "PCM_32", "FLOAT", "DOUBLE"}
                    or wav.samplerate not in _RATES or
                    wav.channels not in (1, 2) or not 1 <= wav.frames <= wav.samplerate * 20):
                raise ValueError("Expected bounded mono/stereo WAV, supported rate, <=20 seconds")
            rate = wav.samplerate
            widths = {"PCM_16": 2, "PCM_24": 3, "PCM_32": 4, "FLOAT": 4, "DOUBLE": 8}
            if data_size != wav.frames * wav.channels * widths[wav.subtype]:
                raise ValueError("Truncated or partial WAV sample frame")
            audio = wav.read(dtype="float64", always_2d=True)
            if len(audio) != wav.frames:
                raise ValueError("Truncated WAV data")
    except (RuntimeError, sf.LibsndfileError) as exc:
        raise ValueError(f"Unreadable WAV: {exc}") from exc
    if not np.isfinite(audio).all():
        raise ValueError("Non-finite capture samples")
    # Reject PCM full scale as well as float excursions; no clipped sample is allowed.
    if np.max(np.abs(audio)) >= 32767 / 32768:
        raise ValueError("Clipping/full-scale samples in WAV")
    return audio, rate, _digest(payload)


def _configuration(configuration: dict, rate: int) -> tuple[dict, str]:
    if not isinstance(configuration, dict):
        raise ValueError("Explicit configuration object required")
    required = {"audio_path", "device", "driver", "sample_rate", "buffer_size_samples", "settings"}
    if set(configuration) != required:
        raise ValueError(f"Configuration requires exactly {sorted(required)}")
    for key in ("audio_path", "device", "driver"):
        if not isinstance(configuration[key], str) or not configuration[key].strip():
            raise ValueError(f"configuration.{key} must describe the capture path")
    if type(configuration["sample_rate"]) is not int or configuration["sample_rate"] != rate:
        raise ValueError("Configuration sample rate must match probe")
    buffer = configuration["buffer_size_samples"]
    if type(buffer) is not int or not 1 <= buffer <= 65536:
        raise ValueError("configuration.buffer_size_samples must be 1..65536")
    if not isinstance(configuration["settings"], dict) or not configuration["settings"]:
        raise ValueError("configuration.settings must explicitly describe relevant audio settings")
    serial = _canonical(configuration)
    return json.loads(serial), _digest(serial)


def _reference(probe: dict) -> tuple[np.ndarray, int]:
    if not isinstance(probe, dict) or probe.get("schema") != "film-calibration-probe/v1":
        raise ValueError("Expected film-calibration-probe/v1 receipt")
    try:
        payload, landmarks = _probe_bytes(probe["sample_rate"], probe["channels"], probe["seed"])
        if (_canonical({"landmarks": probe["landmarks"]}) != _canonical({"landmarks": landmarks})
                or type(probe["sample_count"]) is not int
                or probe["sample_count"] != probe["sample_rate"] * 5
                or probe["sha256"] != _digest(payload)):
            raise ValueError("Probe receipt differs from deterministic reference")
        audio, rate, digest = _read_wav(probe["path"])
    except KeyError as exc:
        raise ValueError(f"Incomplete probe receipt: {exc}") from exc
    if digest != probe["sha256"]:
        raise ValueError("Probe WAV hash mismatch")
    return audio, rate


def _match(signal: np.ndarray, template: np.ndarray) -> tuple[int, float, float, float]:
    n = len(template)
    if len(signal) < n:
        raise ValueError("Capture too short for probe landmark")
    # Normalised matched filtering, not a silence/onset detector. Abs correlation
    # permits gain and polarity changes; each channel retains independent evidence.
    cross = fftconvolve(signal, template[::-1], mode="valid")
    energy = np.concatenate(([0.0], np.cumsum(signal * signal)))
    window_energy = np.maximum(0.0, energy[n:] - energy[:-n])
    template_energy = float(template @ template)
    denom = np.sqrt(window_energy * template_energy)
    score = np.divide(np.abs(cross), denom, out=np.zeros_like(cross), where=denom > 1e-12)
    position = int(np.argmax(score))
    peak = float(min(1.0, score[position]))
    if peak < _MIN_CORRELATION:
        raise ValueError("Missing/weak probe landmark: correlation below 0.90")
    score[max(0, position - 2):position + 3] = 0
    secondary = float(np.max(score))
    if secondary >= _MAX_SECONDARY:
        raise ValueError("Ambiguous/duplicate probe landmark: secondary correlation >=0.70")
    gain = float(cross[position] / template_energy)
    return position, peak, secondary, gain


def measure_capture_calibration(probe: dict, capture_paths: list[str], configuration: dict) -> dict:
    """Measure 2..5 distinct supplied captures; no application or audio-setting calls.

    Positive offset means recorded probe occurs later than reference. Every event,
    channel and repeat must agree exactly at integer-sample resolution.
    """
    if not isinstance(capture_paths, list) or not 2 <= len(capture_paths) <= 5:
        raise ValueError("Supply 2..5 independently recorded probe captures")
    reference, rate = _reference(probe)
    config, config_hash = _configuration(configuration, rate)
    paths = [_path(path) for path in capture_paths]
    probe_path = _path(probe["path"])
    for i, path in enumerate(paths):
        if path.samefile(probe_path):
            raise ValueError("The reference probe cannot count as a recorded capture")
        if any(path.samefile(previous) for previous in paths[:i]):
            raise ValueError("Duplicate capture file is not a separate recording")
    captures, hashes, offsets, correlations, secondary_peaks = [], set(), [], [], []
    for path in capture_paths:
        audio, capture_rate, digest = _read_wav(path)
        if capture_rate != rate:
            raise ValueError("Capture sample rate mismatch; resampling is not calibration")
        if audio.shape[1] != reference.shape[1]:
            raise ValueError("Capture channel count mismatch")
        hashes.add(digest)
        matches = []
        for channel in range(reference.shape[1]):
            for i, event in enumerate(probe["landmarks"]):
                start, count = event["start_sample"], event["sample_count"]
                pos, corr, secondary, gain = _match(audio[:, channel], reference[start:start + count, channel])
                matches.append({"landmark": i, "channel": channel, "reference_sample": start,
                                "capture_sample": pos, "offset_samples": pos - start,
                                "correlation": corr, "secondary_correlation": secondary,
                                "gain": gain, "polarity": 1 if gain >= 0 else -1})
                correlations.append(corr)
                secondary_peaks.append(secondary)
        delays = {match["offset_samples"] for match in matches}
        if len(delays) != 1:
            raise ValueError("Inconsistent landmark/channel delays: drift or channel skew")
        delay = delays.pop()
        if len(audio) < len(reference) + delay:
            raise ValueError("Truncated capture: reference tail is not fully covered")
        offsets.append(delay)
        captures.append({"path": str(_path(path)), "sha256": digest,
                         "sample_count": len(audio), "offset_samples": delay, "matches": matches})
    if len(set(offsets)) != 1:
        raise ValueError("Inconsistent repeat offsets; no reusable calibration")
    receipt = {"schema": "film-calibration/v1", "status": "measured", "probe": json.loads(_canonical(probe)),
               "configuration": config, "configuration_sha256": config_hash,
               "configuration_verified": False, "capture_provenance_verified": False,
               "identical_capture_content": len(hashes) != len(captures), "sample_rate": rate,
               "offset_samples": offsets[0], "correction_samples": -offsets[0],
               "offset_seconds": offsets[0] / rate, "repeat_spread_samples": 0,
               "captures": captures,
               "confidence": {"minimum_correlation": min(correlations),
                              "maximum_secondary_correlation": max(secondary_peaks),
                              "required_minimum_correlation": _MIN_CORRELATION,
                              "maximum_allowed_secondary_correlation": _MAX_SECONDARY,
                              "landmark_spread_samples": 0, "resolution_samples": 1},
               "limitations": ["Caller-described settings and capture provenance are not independently verified",
                               "Integer-sample relative alignment only; no absolute picture-origin proof",
                               "Valid only for the exact declared path/settings; no automatic correction"]}
    receipt["profile_sha256"] = _digest(_canonical(receipt))
    return receipt


def validate_capture_calibration(profile: dict, configuration: dict) -> dict:
    """Read-only remeasurement: reject changed configuration, evidence or profile."""
    if not isinstance(profile, dict) or profile.get("schema") != "film-calibration/v1":
        raise ValueError("Expected film-calibration/v1 profile")
    try:
        _, config_hash = _configuration(configuration, profile["sample_rate"])
        if config_hash != profile["configuration_sha256"]:
            raise ValueError("Stale calibration: configuration changed")
        unsigned = {k: v for k, v in profile.items() if k != "profile_sha256"}
        if _digest(_canonical(unsigned)) != profile["profile_sha256"]:
            raise ValueError("Calibration profile hash mismatch")
        fresh = measure_capture_calibration(profile["probe"], [c["path"] for c in profile["captures"]], configuration)
        if fresh["profile_sha256"] != profile["profile_sha256"]:
            raise ValueError("Calibration evidence changed; measure again")
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Malformed calibration profile: {exc}") from exc
    return {"schema": "film-calibration-validation/v1", "status": "valid",
            "profile_sha256": profile["profile_sha256"], "configuration_sha256": config_hash,
            "offset_samples": fresh["offset_samples"], "correction_samples": fresh["correction_samples"],
            "configuration_verified": False, "evidence_remeasured": True}
