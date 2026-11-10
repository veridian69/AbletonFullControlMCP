"""Sample-exact packaging of immutable WAV captures with explicit origins.

No Live access, resampling, gain changes, silence inference or padding. Timing
is defined in picture frames and source samples, never by the current BPM.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re

import numpy as np
import soundfile as sf

_BLOCK = 65536
_PCM24_MAX = 1 - 2 ** -23


def _integer(value, name, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError(f"{name} must be an integer" + (f" >= {minimum}" if minimum is not None else ""))
    return value


def _clock(frame_rate, start_frame, end_frame, tail_frames, sample_rate):
    if not isinstance(frame_rate, dict):
        raise ValueError("frame_rate must contain integer numerator and denominator")
    n = _integer(frame_rate.get("numerator"), "numerator", 1)
    d = _integer(frame_rate.get("denominator"), "denominator", 1)
    if not 1 <= Fraction(n, d) <= 240:
        raise ValueError("frame rate must be between 1 and 240")
    start = _integer(start_frame, "start_frame", 0)
    end = _integer(end_frame, "end_frame", 1)
    tail = _integer(tail_frames, "tail_frames", 0)
    rate = _integer(sample_rate, "sample_rate", 8000)
    if end <= start or rate > 384000:
        raise ValueError("end_frame must exceed start_frame; sample rate must be <= 384000")
    # Round absolute boundaries once, half-up. Subtracting rounded endpoints
    # avoids a one-sample NTSC error from rounding the duration separately.
    def boundary(frame):
        value = Fraction(frame * rate * d, n)
        return (2 * value.numerator + value.denominator) // (2 * value.denominator)
    a, b, c = boundary(start), boundary(end), boundary(end + tail)
    if a > 2 ** 53 or c > 2 ** 53:
        raise ValueError("timeline exceeds exact interoperable sample coordinates")
    return {"frame_rate": {"numerator": n, "denominator": d},
            "start_frame": start, "end_frame": end, "tail_frames": tail,
            "sample_rate": rate, "start_sample": a, "end_sample": c,
            "sample_count": c - a, "body_sample_count": b - a,
            "tail_sample_count": c - b}


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError("stem name must be 1-80 ASCII letters/digits/underscores/hyphens")
    if value.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                          *(f"LPT{i}" for i in range(1, 10))}:
        raise ValueError("reserved Windows stem name")
    return value


def _sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def plan_delivery(stems, frame_rate, start_frame, end_frame, tail_frames, sample_rate=48000):
    """Read file headers and plan sample cuts; source_start_sample is mandatory."""
    clock = _clock(frame_rate, start_frame, end_frame, tail_frames, sample_rate)
    if not isinstance(stems, list) or not 1 <= len(stems) <= 64:
        raise ValueError("provide 1-64 stem objects")
    planned, names = [], set()
    for stem in stems:
        if not isinstance(stem, dict):
            raise ValueError("each stem must be an object")
        name = _name(stem.get("name"))
        if name.casefold() in names:
            raise ValueError("duplicate output stem name")
        names.add(name.casefold())
        origin = _integer(stem.get("source_start_sample"), "source_start_sample")
        if not isinstance(stem.get("path"), str):
            raise ValueError("stem path must be a string")
        path = Path(stem["path"]).resolve(strict=True)
        info = sf.info(path)
        if info.format not in ("WAV", "WAVEX", "RF64") or info.samplerate != sample_rate:
            raise ValueError("sources must be WAV files at the requested sample rate; no implicit resampling")
        if info.channels not in (1, 2):
            raise ValueError("delivery supports mono or stereo stems")
        offset = clock["start_sample"] - origin
        if offset < 0 or offset + clock["sample_count"] > info.frames:
            raise ValueError(f"{name}: source does not cover the requested body and tail; no implicit padding")
        if clock["sample_count"] * info.channels * 3 > 0xFFFFFFFF - 4096:
            raise ValueError("output exceeds RIFF WAV size limit; split the delivery")
        planned.append({"name": name, "file": name + ".wav", "source_path": str(path),
                        "source_start_sample": origin, "source_offset_samples": offset,
                        "source_frames": info.frames, "channels": info.channels})
    return {"schema": "film-stems/v1", **clock, "subtype": "PCM_24", "stems": planned,
            "alignment_basis": "caller-supplied source sample origins; not inferred from audio",
            "capture_alignment_verified": False,
            "rounding": "absolute sample boundaries, nearest with half-up ties",
            "processing": "trim only; no gain, fade, padding, resampling or dither"}


def _blocks(path, offset, count):
    with sf.SoundFile(path) as stream:
        stream.seek(offset)
        while count:
            data = stream.read(min(count, _BLOCK), dtype="float64", always_2d=True)
            if not len(data):
                raise ValueError("source became shorter during delivery")
            if not np.isfinite(data).all() or np.any(data < -1) or np.any(data > _PCM24_MAX):
                raise ValueError("non-finite or clipping audio cannot be delivered as PCM_24")
            count -= len(data)
            yield data


def prepare_delivery(stems, frame_rate, start_frame, end_frame, tail_frames,
                     output_dir, sample_rate=48000, confirm_origins=False):
    """Create a new delivery directory; never overwrite captures or outputs."""
    if confirm_origins is not True:
        raise ValueError("confirm_origins=True requires checking source sample origins against picture")
    plan = plan_delivery(stems, frame_rate, start_frame, end_frame, tail_frames, sample_rate)
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError("output directory already exists; choose a new delivery directory")
    if not output.parent.is_dir():
        raise ValueError("output parent must already exist")
    # Validate ALL requested audio before publishing any file. Each source's
    # bytes are rechecked after copying to detect concurrent capture changes.
    for stem in plan["stems"]:
        stem["source_sha256"] = _sha(stem["source_path"])
        # Bind the earlier header plan to this hashed source version. In
        # particular, never relabel a replacement 44.1kHz file as 48kHz.
        info = sf.info(stem["source_path"])
        if (info.samplerate != sample_rate or info.frames != stem["source_frames"]
                or info.channels != stem["channels"] or info.format not in ("WAV", "WAVEX", "RF64")
                or _sha(stem["source_path"]) != stem["source_sha256"]):
            raise ValueError("source header/content changed after planning")
        peak = 0.0
        for block in _blocks(stem["source_path"], stem["source_offset_samples"], plan["sample_count"]):
            peak = max(peak, float(np.abs(block).max()))
        stem["peak"] = peak
        last_count = min(plan["sample_count"], max(1, sample_rate // 100))
        last_offset = stem["source_offset_samples"] + plan["sample_count"] - last_count
        stem["tail_not_quiet"] = any(float(np.abs(b).max()) > .001
                                     for b in _blocks(stem["source_path"], last_offset, last_count))
    output.mkdir()  # exclusive reservation; no existing files are touched
    directory_identity = (output.stat().st_dev, output.stat().st_ino)
    owned = []
    try:
        for stem in plan["stems"]:
            target = output / stem["file"]
            with target.open("xb") as raw:
                stat = os.fstat(raw.fileno())
                owned.append((target, (stat.st_dev, stat.st_ino)))
                with sf.SoundFile(raw, mode="w", samplerate=sample_rate, channels=stem["channels"],
                                  format="WAV", subtype="PCM_24") as dest:
                    for data in _blocks(stem["source_path"], stem["source_offset_samples"], plan["sample_count"]):
                        dest.write(data)
            if _sha(stem["source_path"]) != stem["source_sha256"]:
                raise ValueError("source changed during delivery; output rejected")
            stem["sha256"] = _sha(target)
        manifest = output / "manifest.json"
        with manifest.open("x", encoding="utf-8") as stream:
            stat = os.fstat(stream.fileno())
            owned.append((manifest, (stat.st_dev, stat.st_ino)))
            json.dump(plan, stream, indent=2, allow_nan=False)
            stream.write("\n")
        audit = audit_delivery(str(output))
        if audit["status"] != "passed":
            raise ValueError("delivery read-back audit failed: " + str(audit["errors"]))
        return {**plan, "status": "prepared", "output_dir": str(output), "audit": audit}
    except BaseException:
        # Only paths exclusively created by this invocation; never recursive.
        try:
            stat = output.lstat()
            if (stat.st_dev, stat.st_ino) == directory_identity:
                for path, identity in reversed(owned):
                    try:
                        stat = path.lstat()
                        if (stat.st_dev, stat.st_ino) == identity:
                            path.unlink()
                    except OSError:
                        pass
                output.rmdir()
        except OSError:
            pass  # leave any independently created files untouched
        raise


def audit_delivery(output_dir):
    """Verify delivered WAV headers, frame counts, finite samples and hashes."""
    root = Path(output_dir).resolve(strict=True)
    path = root / "manifest.json"
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("manifest too large")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    if manifest.get("schema") != "film-stems/v1" or manifest.get("subtype") != "PCM_24":
        raise ValueError("unsupported delivery manifest")
    clock = _clock(*(manifest[k] for k in ("frame_rate", "start_frame", "end_frame", "tail_frames", "sample_rate")))
    errors = [f"clock field differs: {k}" for k, v in clock.items() if manifest.get(k) != v]
    stems = manifest.get("stems")
    if not isinstance(stems, list) or not 1 <= len(stems) <= 64:
        raise ValueError("invalid manifest stems")
    names = set()
    for stem in stems:
        if not isinstance(stem, dict):
            raise ValueError("manifest stem must be an object")
        name = _name(stem.get("name"))
        if name.casefold() in names or stem.get("file") != name + ".wav":
            raise ValueError("invalid or duplicate manifest filename")
        names.add(name.casefold())
        target = root / stem["file"]
        if target.is_symlink() or target.resolve().parent != root:
            raise ValueError("delivery file escapes its directory")
        try:
            info = sf.info(target)
            if (info.frames != clock["sample_count"] or info.samplerate != clock["sample_rate"]
                    or info.channels != stem.get("channels") or info.subtype != "PCM_24"
                    or info.format != "WAV"):
                errors.append(name + ": WAV format/count mismatch")
            if _sha(target) != stem.get("sha256"):
                errors.append(name + ": SHA-256 mismatch")
            for _ in _blocks(target, 0, info.frames):
                pass
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(name + ": " + str(exc))
    return {"status": "failed" if errors else "passed", "errors": errors,
            "sample_count": clock["sample_count"], "sample_rate": clock["sample_rate"],
            "stem_count": len(stems), "capture_alignment_verified": False}
