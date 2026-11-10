"""Real DSP fixtures; no Live client or mocked calibration evidence."""
import hashlib
import importlib
import importlib.util
import json
import struct

import numpy as np
import pytest
import soundfile as sf


@pytest.fixture
def api():
    name = "ableton_mcp.bounce.film_calibration"
    assert importlib.util.find_spec(name) is not None, "calibration implementation missing"
    return importlib.import_module(name)


def configuration(rate=32000):
    return {"audio_path": "disposable-set/master/resampling/no-effects",
            "device": "synthetic-test-device", "driver": "fixture",
            "sample_rate": rate, "buffer_size_samples": 256,
            "settings": {"delay_compensation": True, "monitoring": "off"}}


def capture(tmp_path, data, rate, name, offset=0, gain=1, noise=0.00001):
    # Positive means recorded landmarks are later; negative removes only lead silence.
    shifted = np.pad(data, ((max(0, offset), 4096), (0, 0)))[max(0, -offset):]
    shifted = shifted * gain
    shifted += np.random.default_rng(name).normal(0, noise, shifted.shape)
    path = tmp_path / f"capture-{name}.wav"
    sf.write(path, shifted, rate, subtype="FLOAT")
    return str(path)


def setup_probe(api, tmp_path, channels=1):
    probe = api.generate_calibration_probe(str(tmp_path / "probe.wav"), 32000, channels, 73)
    data, rate = sf.read(probe["path"], always_2d=True)
    return probe, data, rate


def measure(api, probe, paths, rate=32000):
    return api.measure_capture_calibration(probe, paths, configuration(rate))


def test_probe_reproducible_distinct_landmarks_and_exclusive_output(api, tmp_path):
    a, data, rate = setup_probe(api, tmp_path, 2)
    b = api.generate_calibration_probe(str(tmp_path / "again.wav"), rate, 2, 73)
    c = api.generate_calibration_probe(str(tmp_path / "other.wav"), rate, 2, 74)
    assert a["sha256"] == b["sha256"] != c["sha256"]
    assert data.shape == (160000, 2) and np.max(np.abs(data)) < 0.5
    assert len(a["landmarks"]) == 4
    windows = [data[e["start_sample"]:e["start_sample"] + e["sample_count"], ch]
               for e in a["landmarks"] for ch in range(2)]
    assert len({w.tobytes() for w in windows}) == 8
    original = (tmp_path / "probe.wav").read_bytes()
    with pytest.raises(FileExistsError):
        api.generate_calibration_probe(a["path"], rate, 2, 99)
    assert (tmp_path / "probe.wav").read_bytes() == original


@pytest.mark.parametrize("offset,gain,channels", [(0, 1, 1), (731, .4, 1),
                                                  (-217, -.7, 1), (1234, -.5, 2)])
def test_measures_signed_offsets_gain_polarity_stereo_and_noise(api, tmp_path, offset, gain, channels):
    probe, data, rate = setup_probe(api, tmp_path, channels)
    paths = [capture(tmp_path, data, rate, n, offset, gain, .002) for n in (1, 2)]
    result = measure(api, probe, paths)
    assert result["schema"] == "film-calibration/v1"
    assert result["status"] == "measured"
    assert result["offset_samples"] == offset
    assert result["correction_samples"] == -offset
    assert result["offset_seconds"] == pytest.approx(offset / rate)
    assert result["repeat_spread_samples"] == 0
    assert result["confidence"]["minimum_correlation"] > .9
    assert len(result["captures"]) == 2
    for item in result["captures"]:
        assert len(item["matches"]) == 4 * channels
        assert {m["offset_samples"] for m in item["matches"]} == {offset}
        assert item["sha256"] == hashlib.sha256(open(item["path"], "rb").read()).hexdigest()
    assert result["configuration"] == configuration()
    assert result["configuration_verified"] is False


@pytest.mark.parametrize("kind", ["silence", "noise", "clip", "nonfinite", "periodic",
                                  "duplicate", "drift", "short", "tail_truncated", "channel"])
def test_bad_signal_cannot_be_reported_measured(api, tmp_path, kind):
    probe, data, rate = setup_probe(api, tmp_path)
    if kind == "silence":
        data[:] = 0
    elif kind == "noise":
        data = np.random.default_rng(17).normal(0, .05, data.shape)
    elif kind == "clip":
        data[5] = 1.0
    elif kind == "nonfinite":
        data[5] = np.nan
    elif kind == "periodic":
        data[:, 0] = .2 * np.sin(np.arange(len(data)) * .1)
    elif kind == "duplicate":
        data = np.concatenate([data, data])
    elif kind == "drift":
        moved = np.zeros_like(data)
        for i, event in enumerate(probe["landmarks"]):
            p, n = event["start_sample"], event["sample_count"]
            moved[p + i * 4:p + i * 4 + n] = data[p:p + n]
        data = moved
    elif kind == "short":
        data = data[:1000]
    elif kind == "tail_truncated":
        data = data[:140000]  # All landmarks survive; full reference coverage does not.
    elif kind == "channel":
        data = np.column_stack([data, data])
    paths = [capture(tmp_path, data, rate, n, noise=1e-8) for n in (1, 2)]
    # Independent tiny noise prevents duplicate-file rejection masking DSP checks.
    expected = {"silence": "weak", "noise": "weak", "clip": "Clipping", "nonfinite": "Non-finite",
                "periodic": "weak", "duplicate": "Ambiguous", "drift": "Inconsistent",
                "short": "weak|short", "tail_truncated": "Truncated", "channel": "channel"}
    with pytest.raises(ValueError, match=expected[kind]):
        measure(api, probe, paths)


def test_rate_mismatch_and_repeat_inconsistency(api, tmp_path):
    probe, data, rate = setup_probe(api, tmp_path)
    a = capture(tmp_path, data, rate, 1, 42)
    b = capture(tmp_path, data, rate, 2, 44)
    with pytest.raises(ValueError, match="repeat|consistent"):
        measure(api, probe, [a, b])
    c = capture(tmp_path, data, 44100, 3, 42)
    with pytest.raises(ValueError, match="rate"):
        measure(api, probe, [a, c])


def test_requires_multiple_capture_files_not_repeated_paths(api, tmp_path):
    probe, data, rate = setup_probe(api, tmp_path)
    a = capture(tmp_path, data, rate, 1)
    for paths in ([a], [a, a], [a] * 6):
        with pytest.raises(ValueError):
            measure(api, probe, paths)


@pytest.mark.parametrize("reference_kind", ["direct", "path_alias", "hardlink"])
def test_probe_cannot_count_as_a_recorded_capture(api, tmp_path, reference_kind):
    probe, data, rate = setup_probe(api, tmp_path)
    recorded = capture(tmp_path, data, rate, 1, offset=0)
    reference = probe["path"]
    if reference_kind == "path_alias":
        reference = str(tmp_path) + "/./probe.wav"
    elif reference_kind == "hardlink":
        link = tmp_path / "probe-hardlink.wav"
        link.hardlink_to(probe["path"])
        reference = str(link)
    with pytest.raises(ValueError, match="probe.*capture|capture.*probe"):
        measure(api, probe, [reference, recorded])


def test_validation_refuses_previously_issued_probe_as_capture_profile(api, tmp_path):
    probe, data, rate = setup_probe(api, tmp_path)
    recorded = capture(tmp_path, data, rate, 1, offset=0)
    copy = tmp_path / "separate-file.wav"
    copy.write_bytes((tmp_path / "probe.wav").read_bytes())
    profile = measure(api, probe, [str(copy), recorded])
    # Reconstruct the exact old receipt without relying on a disabled guard:
    # identical bytes produce identical DSP evidence; only path/hash changes.
    profile["captures"][0]["path"] = probe["path"]
    profile.pop("profile_sha256")
    profile["profile_sha256"] = hashlib.sha256(json.dumps(
        profile, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    with pytest.raises(ValueError, match="probe.*capture|capture.*probe"):
        api.validate_capture_calibration(profile, configuration())


@pytest.mark.parametrize("kwargs", [{"sample_rate": 0}, {"sample_rate": 192000},
                                    {"sample_rate": True}, {"channels": 3},
                                    {"channels": True}, {"seed": -1}, {"seed": 1.5}])
def test_invalid_probe_parameters_never_write(api, tmp_path, kwargs):
    path = tmp_path / "not-created.wav"
    with pytest.raises(ValueError):
        api.generate_calibration_probe(str(path), **kwargs)
    assert not path.exists()


def test_probe_receipt_or_pcm_tamper_refused(api, tmp_path):
    probe, data, rate = setup_probe(api, tmp_path)
    paths = [capture(tmp_path, data, rate, n) for n in (1, 2)]
    altered = json.loads(json.dumps(probe))
    altered["landmarks"][0]["start_sample"] += 1
    with pytest.raises(ValueError):
        measure(api, altered, paths)
    sf.write(probe["path"], np.zeros_like(data), rate, subtype="PCM_16")
    probe["sha256"] = hashlib.sha256(open(probe["path"], "rb").read()).hexdigest()
    with pytest.raises(ValueError):
        measure(api, probe, paths)


def test_profile_binds_configuration_and_rechecks_files_and_receipt(api, tmp_path):
    probe, data, rate = setup_probe(api, tmp_path)
    paths = [capture(tmp_path, data, rate, n, 250) for n in (1, 2)]
    receipt = measure(api, probe, paths)
    assert api.validate_capture_calibration(receipt, configuration())["status"] == "valid"
    changed = configuration(); changed["buffer_size_samples"] = 512
    with pytest.raises(ValueError, match="configuration"):
        api.validate_capture_calibration(receipt, changed)
    altered = json.loads(json.dumps(receipt)); altered["offset_samples"] = 0
    with pytest.raises(ValueError):
        api.validate_capture_calibration(altered, configuration())
    sf.write(paths[0], np.zeros_like(data), rate, subtype="PCM_16")
    with pytest.raises(ValueError):
        api.validate_capture_calibration(receipt, configuration())


@pytest.mark.parametrize("key,value", [("device", ""), ("audio_path", None),
                                      ("driver", ""), ("sample_rate", 44100),
                                      ("buffer_size_samples", True), ("settings", {})])
def test_configuration_is_explicit_and_rate_bound(api, tmp_path, key, value):
    probe, data, rate = setup_probe(api, tmp_path)
    paths = [capture(tmp_path, data, rate, n) for n in (1, 2)]
    cfg = configuration(); cfg[key] = value
    with pytest.raises(ValueError):
        api.measure_capture_calibration(probe, paths, cfg)


@pytest.mark.parametrize("rate", [44100, 48000, 88200, 96000])
def test_supported_rates_measure_at_native_rate(api, tmp_path, rate):
    probe = api.generate_calibration_probe(str(tmp_path / "probe.wav"), rate, 1, 11)
    data, _ = sf.read(probe["path"], always_2d=True)
    paths = [capture(tmp_path, data, rate, n, 701) for n in (1, 2)]
    assert measure(api, probe, paths, rate)["offset_samples"] == 701


def test_channel_skew_refused_not_averaged(api, tmp_path):
    probe, data, rate = setup_probe(api, tmp_path, 2)
    data[:, 1] = np.roll(data[:, 1], 3)
    paths = [capture(tmp_path, data, rate, n) for n in (1, 2)]
    with pytest.raises(ValueError, match="channel"):
        measure(api, probe, paths)


def test_capture_duration_is_bounded_before_dsp(api, tmp_path):
    probe, data, rate = setup_probe(api, tmp_path)
    path = capture(tmp_path, np.zeros((rate * 21, 1)), rate, 1)
    other = capture(tmp_path, data, rate, 2)
    with pytest.raises(ValueError, match="bounded"):
        measure(api, probe, [path, other])


def test_bit_identical_digital_repeats_are_evidence_not_verified_provenance(api, tmp_path):
    probe, data, rate = setup_probe(api, tmp_path)
    paths = [capture(tmp_path, data, rate, n, 256, noise=0) for n in (1, 2)]
    result = measure(api, probe, paths)
    assert result["offset_samples"] == 256
    assert result["capture_provenance_verified"] is False
    assert result["identical_capture_content"] is True


def test_truncated_riff_container_refused_even_when_all_probe_audio_survives(api, tmp_path):
    probe, data, rate = setup_probe(api, tmp_path)
    a = capture(tmp_path, data, rate, 1)
    b = capture(tmp_path, data, rate, 2)
    # libsndfile accepts missing tail data by reducing frames. Our full 5s probe
    # still fits after this truncation, so reference coverage alone cannot catch it.
    path = tmp_path / "capture-1.wav"
    path.write_bytes(path.read_bytes()[:-1000])
    with pytest.raises(ValueError, match="RIFF|container|Truncated"):
        measure(api, probe, [a, b])


def test_partial_sample_frame_refused_even_with_consistent_riff_length(api, tmp_path):
    probe, data, rate = setup_probe(api, tmp_path)
    a = capture(tmp_path, data, rate, 1)
    b = capture(tmp_path, data, rate, 2)
    path = tmp_path / "capture-1.wav"
    raw = bytearray(path.read_bytes()[:-2])
    data_position = raw.index(b"data")
    struct.pack_into("<I", raw, 4, len(raw) - 8)
    struct.pack_into("<I", raw, data_position + 4,
                     struct.unpack_from("<I", raw, data_position + 4)[0] - 2)
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="frame|Truncated"):
        measure(api, probe, [a, b])


def test_float_landmark_metadata_refused_as_malformed_receipt(api, tmp_path):
    probe, data, rate = setup_probe(api, tmp_path)
    paths = [capture(tmp_path, data, rate, n) for n in (1, 2)]
    probe["landmarks"][0]["start_sample"] = 16000.0
    with pytest.raises(ValueError, match="receipt"):
        measure(api, probe, paths)


def test_oversized_file_refused(api, tmp_path):
    probe, data, rate = setup_probe(api, tmp_path)
    path = tmp_path / "oversize.wav"
    with path.open("wb") as stream:
        stream.seek(32 * 1024 * 1024)
        stream.write(b"x")
    other = capture(tmp_path, data, rate, 2)
    with pytest.raises(ValueError, match="32 MiB"):
        measure(api, probe, [str(path), other])


def test_canonical_configuration_and_profile_hash_contract(api, tmp_path):
    probe, data, rate = setup_probe(api, tmp_path)
    paths = [capture(tmp_path, data, rate, n) for n in (1, 2)]
    cfg = configuration(); cfg["device"] = "Zürich interface"
    result = api.measure_capture_calibration(probe, paths, cfg)
    def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert result["configuration_sha256"] == hashlib.sha256(canonical(cfg)).hexdigest()
    unsigned = dict(result); unsigned.pop("profile_sha256")
    assert result["profile_sha256"] == hashlib.sha256(canonical(unsigned)).hexdigest()


@pytest.mark.asyncio
async def test_mcp_tools_generate_measure_validate_and_return_errors(tmp_path):
    from mcp.server.fastmcp import FastMCP
    name = "ableton_mcp.tools.film_calibration"
    assert importlib.util.find_spec(name) is not None, "calibration MCP tools missing"
    tool_module = importlib.import_module(name)
    server = FastMCP("offline-calibration-test")
    tool_module.register(server)
    async def call(name, args):
        result = await server.call_tool(name, args)
        return json.loads(result[0][0].text if isinstance(result, tuple) else result[0].text)
    args = {"output_path": str(tmp_path / "probe.wav"), "sample_rate": 32000, "channels": 1, "seed": 4}
    probe = await call("generate_calibration_probe", args)
    assert probe["schema"] == "film-calibration-probe/v1"
    assert (await call("generate_calibration_probe", args))["status"] == "error"
    data, rate = sf.read(probe["path"], always_2d=True)
    paths = [capture(tmp_path, data, rate, n, -417) for n in (1, 2)]
    receipt = await call("measure_capture_calibration", {
        "probe": probe, "capture_paths": paths, "configuration": configuration()})
    assert receipt["offset_samples"] == -417
    valid = await call("validate_capture_calibration", {"profile": receipt, "configuration": configuration()})
    assert valid["status"] == "valid" and valid["evidence_remeasured"] is True
    cfg = configuration(); cfg["device"] = "different"
    failed = await call("validate_capture_calibration", {"profile": receipt, "configuration": cfg})
    assert failed["status"] == "error" and "configuration" in failed["error"]
