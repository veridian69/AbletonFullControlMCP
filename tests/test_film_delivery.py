"""Real WAV tests: catch clock, alignment, overwrite and audit failures."""
import hashlib
import json

import numpy as np
import pytest
import soundfile as sf

from ableton_mcp.bounce.film_delivery import plan_delivery, prepare_delivery, audit_delivery


def source(tmp_path, name="score", frames=12000, origin=0, impulse=2000, rate=48000):
    path = tmp_path / (name + ".wav")
    audio = np.zeros((frames, 2))
    if impulse is not None:
        audio[impulse] = [.25, -.125]
    sf.write(path, audio, rate, subtype="PCM_24")
    return {"name": name, "path": str(path), "source_start_sample": origin}


def params(stems, **kwargs):
    return dict(stems=stems, frame_rate={"numerator": 24, "denominator": 1},
                start_frame=0, end_frame=4, tail_frames=1, sample_rate=48000, **kwargs)


def test_exact_common_start_tail_and_source_immutability(tmp_path):
    a = source(tmp_path)
    b = source(tmp_path, "foley", origin=-1000, impulse=3000)
    originals = [hashlib.sha256(open(s["path"], "rb").read()).hexdigest() for s in (a, b)]
    result = prepare_delivery(**params([a, b]), output_dir=str(tmp_path / "delivery"), confirm_origins=True)
    assert result["sample_count"] == 10000 and result["body_sample_count"] == 8000
    assert result["start_sample"] == 0 and result["tail_sample_count"] == 2000
    for stem in result["stems"]:
        data, rate = sf.read(tmp_path / "delivery" / stem["file"], always_2d=True)
        assert rate == 48000 and data.shape == (10000, 2)
        assert np.flatnonzero(data[:, 0]).tolist() == [2000]
        assert data[2000].tolist() == [.25, -.125]
        assert sf.info(tmp_path / "delivery" / stem["file"]).subtype == "PCM_24"
    assert originals == [hashlib.sha256(open(s["path"], "rb").read()).hexdigest() for s in (a, b)]
    audit = audit_delivery(str(tmp_path / "delivery"))
    assert audit["status"] == "passed" and audit["sample_count"] == 10000


def test_ntsc_rounds_endpoints_not_duration(tmp_path):
    stem = source(tmp_path, frames=6000)
    plan = plan_delivery([stem], {"numerator": 30000, "denominator": 1001}, 1, 2, 0, 48000)
    # At 29.97 fps: frame1=1601.6->1602, frame2=3203.2->3203.
    assert plan["start_sample"] == 1602 and plan["end_sample"] == 3203
    assert plan["sample_count"] == 1601


@pytest.mark.parametrize("mutation", ["missing_origin", "short", "rate", "name", "bool_origin", "duplicate"])
def test_invalid_inputs_never_create_output(tmp_path, mutation):
    s = source(tmp_path, frames=5000 if mutation == "short" else 12000,
               rate=44100 if mutation == "rate" else 48000)
    if mutation == "missing_origin":
        s.pop("source_start_sample")
    if mutation == "name":
        s["name"] = "../escape"
    if mutation == "bool_origin":
        s["source_start_sample"] = True
    stems = [s, dict(s)] if mutation == "duplicate" else [s]
    out = tmp_path / "delivery"
    with pytest.raises(ValueError):
        prepare_delivery(**params(stems), output_dir=str(out), confirm_origins=True)
    assert not out.exists()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1.1, -1.1])
def test_nonfinite_or_clipping_audio_rejected_without_outputs(tmp_path, value):
    s = source(tmp_path)
    data = np.zeros((12000, 2)); data[20] = value
    sf.write(s["path"], data, 48000, subtype="FLOAT")
    with pytest.raises(ValueError):
        prepare_delivery(**params([s]), output_dir=str(tmp_path / "delivery"), confirm_origins=True)
    assert not (tmp_path / "delivery").exists()


def test_explicit_origin_confirmation_and_no_overwrite(tmp_path):
    s = source(tmp_path)
    out = tmp_path / "delivery"
    with pytest.raises(ValueError, match="confirm"):
        prepare_delivery(**params([s]), output_dir=str(out))
    out.mkdir(); marker = out / "keep.txt"; marker.write_text("user-owned")
    with pytest.raises(FileExistsError):
        prepare_delivery(**params([s]), output_dir=str(out), confirm_origins=True)
    assert marker.read_text() == "user-owned"


def test_audit_detects_changed_audio_and_rejects_escape(tmp_path):
    s = source(tmp_path)
    out = tmp_path / "delivery"
    prepare_delivery(**params([s]), output_dir=str(out), confirm_origins=True)
    with sf.SoundFile(out / "score.wav", "r+") as f:
        f.seek(3000); f.write(np.ones((1, 2)) * .1)
    assert audit_delivery(str(out))["status"] == "failed"
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["stems"][0]["file"] = "../score.wav"
    (out / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        audit_delivery(str(out))


def test_tail_warning_does_not_fade_or_normalise(tmp_path):
    s = source(tmp_path, impulse=9999)
    out = tmp_path / "delivery"
    result = prepare_delivery(**params([s]), output_dir=str(out), confirm_origins=True)
    assert result["stems"][0]["tail_not_quiet"] is True
    data, _ = sf.read(out / "score.wav")
    assert data[-1].tolist() == [.25, -.125]


@pytest.mark.asyncio
async def test_mcp_preview_and_confirmed_delivery(tmp_path):
    from mcp.server.fastmcp import FastMCP
    from ableton_mcp.tools.film_delivery import register
    mcp = FastMCP("film-delivery-test")
    register(mcp)
    s = source(tmp_path)
    out = tmp_path / "delivery"
    args = {**params([s]), "output_dir": str(out)}
    _, preview = await mcp.call_tool("film_stems_prepare", args)
    assert preview["status"] == "preview" and not out.exists()
    _, refused = await mcp.call_tool("film_stems_prepare", {**args, "preview": False})
    assert refused["status"] == "error" and not out.exists()
    _, written = await mcp.call_tool("film_stems_prepare", {**args, "preview": False, "confirm_origins": True})
    assert written["status"] == "prepared" and out.is_dir()
    _, audited = await mcp.call_tool("film_stems_audit", {"output_dir": str(out)})
    assert audited["status"] == "passed"


def test_source_rate_change_after_plan_is_rejected(tmp_path, monkeypatch):
    from ableton_mcp.bounce import film_delivery as module
    s = source(tmp_path)
    original_sha = module._sha
    changed = False
    def sha_after_replacement(path):
        nonlocal changed
        if not changed:
            changed = True
            sf.write(s["path"], np.zeros((12000, 2)), 44100, subtype="PCM_24")
        return original_sha(path)
    monkeypatch.setattr(module, "_sha", sha_after_replacement)
    with pytest.raises(ValueError):
        prepare_delivery(**params([s]), output_dir=str(tmp_path / "delivery"), confirm_origins=True)
    assert not (tmp_path / "delivery").exists()


def test_failure_cleanup_preserves_replacement_file(tmp_path, monkeypatch):
    from ableton_mcp.bounce import film_delivery as module
    s = source(tmp_path)
    out = tmp_path / "delivery"
    def failed_audit(_):
        (out / "score.wav").rename(out / "original-generated.wav")
        (out / "score.wav").write_bytes(b"independent replacement")
        return {"status": "failed", "errors": ["concurrent replacement"]}
    monkeypatch.setattr(module, "audit_delivery", failed_audit)
    with pytest.raises(ValueError):
        prepare_delivery(**params([s]), output_dir=str(out), confirm_origins=True)
    assert (out / "score.wav").read_bytes() == b"independent replacement"


@pytest.mark.parametrize("bad_root", [True, False])
def test_audit_rejects_non_object_manifest_nodes(tmp_path, bad_root):
    out = tmp_path / "delivery"
    prepare_delivery(**params([source(tmp_path)]), output_dir=str(out), confirm_origins=True)
    path = out / "manifest.json"
    data = json.loads(path.read_text())
    if bad_root:
        data = []
    else:
        data["stems"] = [None]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="object"):
        audit_delivery(str(out))
