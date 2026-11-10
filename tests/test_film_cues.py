"""Film timing contracts; Live is replaced only at the document boundary."""

import copy
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def sheet(frames=(240,), fps=(24, 1)):
    return {
        "schema": "film-cues/v1",
        "frame_rate": {"numerator": fps[0], "denominator": fps[1]},
        "timeline": {"name": "Ch5", "start_frame": 86400, "start_timecode": "01:00:00:00"},
        "cues": [
            {"id": f"marker:{f}", "name": "Entrance", "note": "Sparse tonal bed",
             "color": "Blue", "custom_data": "editor note", "start_frame": f,
             "duration_frames": 48, "start_seconds": f * fps[1] / fps[0],
             "duration_seconds": 48 * fps[1] / fps[0]}
            for f in frames
        ],
    }


def plan(data=None, **kwargs):
    from ableton_mcp.film_cues import plan_film_cues

    return plan_film_cues(sheet() if data is None else data, **kwargs)


def test_relative_frames_not_absolute_timecode_and_metadata_preserved():
    data = sheet()
    before = copy.deepcopy(data)
    p = plan(data, tempo_bpm=120, arrangement_start_beats=8, cue_namespace="jim-ch5")
    cue = p["cues"][0]
    assert cue["start_seconds"] == 10
    assert cue["start_beats"] == 28
    assert cue["end_beats"] == 32
    assert cue["note"] == "Sparse tonal bed"
    assert cue["custom_data"] == "editor note"
    assert cue["locator_name"] == "[film:jim-ch5:marker:240] Entrance"
    assert data == before


def test_fractional_frame_rate_uses_rational_rate():
    cue = plan(sheet((24000,), (24000, 1001)), tempo_bpm=60)["cues"][0]
    assert cue["start_seconds"] == 1001
    assert cue["start_beats"] == 1001


def test_piecewise_tempo_integrates_start_and_end_across_boundary():
    p = plan(sheet((216,)), tempo_map=[
        {"start_seconds": 0, "bpm": 120}, {"start_seconds": 10, "bpm": 60},
    ])
    assert p["cues"][0]["start_beats"] == 18
    assert p["cues"][0]["end_beats"] == 21
    assert p["tempo_assumption"]["segments"][1]["start_beats"] == 20
    assert p["tempo_assumption"]["verified_in_live"] is False


@pytest.mark.parametrize("kwargs", [
    {}, {"tempo_bpm": 0}, {"tempo_bpm": float("nan")},
    {"tempo_bpm": True}, {"tempo_bpm": 120, "tempo_map": []},
    {"tempo_map": []}, {"tempo_map": [{"start_seconds": 1, "bpm": 120}]},
    {"tempo_map": [{"start_seconds": 0, "bpm": 120}, {"start_seconds": 0, "bpm": 60}]},
    {"tempo_map": [{"start_seconds": 0, "bpm": -1}]},
    {"tempo_bpm": 120, "arrangement_start_beats": -1},
    {"tempo_bpm": 120, "cue_namespace": "bad]tag"},
])
def test_rejects_ambiguous_or_invalid_timing(kwargs):
    with pytest.raises(ValueError):
        plan(**kwargs)


@pytest.mark.parametrize("field,value", [
    ("start_frame", 1.5), ("start_frame", True), ("start_frame", -1),
    ("duration_frames", -1), ("duration_frames", 0), ("start_seconds", 999),
    ("duration_seconds", float("inf")), ("id", ""), ("name", 3),
])
def test_rejects_bad_cue_fields(field, value):
    data = sheet()
    data["cues"][0][field] = value
    with pytest.raises(ValueError):
        plan(data, tempo_bpm=120)


def test_zero_duration_rejected_even_when_seconds_agree():
    data = sheet()
    data["cues"][0].update(duration_frames=0, duration_seconds=0)
    with pytest.raises(ValueError):
        plan(data, tempo_bpm=120)


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(schema="other/v1"),
    lambda s: s["frame_rate"].update(denominator=0),
    lambda s: s["frame_rate"].update(numerator=23.976),
    lambda s: s["timeline"].pop("start_frame"),
    lambda s: s["cues"].append(copy.deepcopy(s["cues"][0])),
    lambda s: s["cues"][0].pop("note"),
])
def test_schema_errors_are_rejected(mutation):
    data = sheet()
    mutation(data)
    with pytest.raises(ValueError):
        plan(data, tempo_bpm=120)


class Cue:
    def __init__(self, time, name):
        self.time, self.name = time, name


class Song:
    def __init__(self):
        self.tempo = 120.0
        self.is_playing = self.record_mode = self.session_record = False
        self.cue_points = []

    @property
    def current_song_time(self):
        return 7.0

    @current_song_time.setter
    def current_song_time(self, value):
        pytest.fail("Preview must never seek")

    def set_or_delete_cue(self):
        pytest.fail("Preview must never toggle")


@pytest.fixture
def live(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "live_remote_script/AbletonFullControlBridge/handlers/project.py"
    spec = importlib.util.spec_from_file_location("film_test_project_handler", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    song = Song()
    monkeypatch.setitem(sys.modules, "Live", SimpleNamespace(
        Application=SimpleNamespace(get_application=lambda: SimpleNamespace(get_document=lambda: song))
    ))
    return song, module


def invoke(live, *, data=None, preview=True, confirm_tempo=False, **timing):
    song, module = live
    p = plan(data, **({"tempo_bpm": 120} if not timing else timing))
    return module.import_film_cues(
        None, cues=p["cues"], tempo_assumption=p["tempo_assumption"],
        preview=preview, confirm_tempo=confirm_tempo,
    )


def test_preview_default_does_not_touch_live(live):
    song, _ = live
    result = invoke(live)
    assert result["status"] == "preview"
    assert len(result["would_add"]) == 1
    assert song.cue_points == []
    assert song.current_song_time == 7


def test_unrelated_nearby_cue_blocks_entire_batch_without_rename(live):
    song, _ = live
    song.cue_points.append(Cue(20.001, "Human cue"))
    result = invoke(live, data=sheet((120, 240)), preview=True, confirm_tempo=True)
    assert result["status"] == "conflict"
    assert [(c.name, c.time) for c in song.cue_points] == [("Human cue", 20.001)]
    assert song.current_song_time == 7


def test_stale_owned_cue_is_reported_not_moved_or_duplicated(live):
    song, _ = live
    song.cue_points.append(Cue(12, "[film:film:marker:240] Old name"))
    result = invoke(live, preview=True, confirm_tempo=True)
    assert result["status"] == "conflict"
    assert len(song.cue_points) == 1
    assert song.cue_points[0].time == 12


def test_missing_id_in_bridge_payload_refuses_before_mutation(live):
    p = plan(tempo_bpm=120)
    p["cues"][0].pop("id")
    with pytest.raises(ValueError):
        live[1].import_film_cues(None, cues=p["cues"], tempo_assumption=p["tempo_assumption"],
                                 preview=True, confirm_tempo=True)
    assert live[0].cue_points == []


def test_negative_bridge_tempo_origin_refuses_before_mutation(live):
    p = plan(tempo_bpm=120)
    p["tempo_assumption"]["segments"][0]["start_beats"] = -.5
    with pytest.raises(ValueError):
        live[1].import_film_cues(None, cues=p["cues"], tempo_assumption=p["tempo_assumption"],
                                 preview=True, confirm_tempo=True)
    assert live[0].cue_points == []


@pytest.mark.asyncio
async def test_old_bridge_returns_unavailable_without_write(monkeypatch):
    from ableton_mcp.server import build_server
    from ableton_mcp.tools import film_cues

    class OldBridge:
        async def version_info(self, **kwargs):
            return {"handlers": ["project.info"], "bridge_version": "1.6.0"}

        async def call(self, *args, **kwargs):
            pytest.fail("must not attempt a write through an unsupported bridge")

    monkeypatch.setattr(film_cues, "get_bridge_client", lambda: OldBridge())
    _, result = await build_server().call_tool("film_cues_import", {
        "cue_sheet": sheet(), "tempo_bpm": 120, "preview": True, "confirm_tempo": True,
    })
    assert result["status"] == "unavailable"


@pytest.mark.asyncio
async def test_socket_failure_during_handshake_reports_no_write(monkeypatch):
    from ableton_mcp.server import build_server
    from ableton_mcp.tools import film_cues

    class FailingHandshake:
        async def version_info(self, **kwargs):
            raise ConnectionResetError("handshake reset")

        async def call(self, *args, **kwargs):
            pytest.fail("failed handshake must not be followed by an import")

    monkeypatch.setattr(film_cues, "get_bridge_client", lambda: FailingHandshake())
    _, result = await build_server().call_tool("film_cues_import", {
        "cue_sheet": sheet(), "tempo_bpm": 120, "preview": True, "confirm_tempo": True,
    })
    assert result["status"] == "error"
    assert result["write_outcome"] == "not_written"


@pytest.mark.asyncio
async def test_planner_tool_is_offline(monkeypatch):
    from ableton_mcp.server import build_server
    from ableton_mcp.tools import film_cues

    def no_live():
        pytest.fail("planning must not connect to Live")
    monkeypatch.setattr(film_cues, "get_bridge_client", no_live)
    _, result = await build_server().call_tool("film_cues_plan", {
        "cue_sheet": sheet(), "tempo_bpm": 120,
    })
    assert result["status"] == "ok"
    assert result["plan"]["cues"][0]["start_beats"] == 20


@pytest.mark.asyncio
async def test_tools_registered_and_default_import_is_preview(live, monkeypatch):
    from ableton_mcp.server import build_server

    mcp = build_server()
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    assert "film_cues_plan" in tools and "film_cues_import" in tools
    assert tools["film_cues_import"].inputSchema["properties"]["preview"]["default"] is True
    from ableton_mcp.tools import film_cues

    class Bridge:
        async def version_info(self, **kwargs):
            return {"handlers": ["project.import_film_cues", "project.film_cues_step"]}

        async def call(self, op, **kwargs):
            assert op == "project.import_film_cues"
            return live[1].import_film_cues(None, **kwargs)

    monkeypatch.setattr(film_cues, "get_bridge_client", lambda: Bridge())
    _, result = await mcp.call_tool("film_cues_import", {"cue_sheet": sheet(), "tempo_bpm": 120})
    assert result["status"] == "preview"
    assert live[0].cue_points == []
    assert result["plan"]["cues"][0]["start_beats"] == 20


def test_preview_reports_tempo_mismatch_without_writes(live):
    live[0].tempo = 90
    result = invoke(live)
    assert result["tempo_check"]["status"] == "mismatch"
    assert result["writes_enabled"] is False
    assert result["write_outcome"] == "not_written"
    assert result["transport_before"] == result["transport_after"]
    assert not live[1]._FILM_CUE_JOBS


def test_preview_before_picture_origin_does_not_extrapolate_tempo(live):
    live[0].tempo = 90
    result = invoke(live, arrangement_start_beats=16, tempo_bpm=120)
    assert result["tempo_check"]["status"] == "outside_coverage"
    assert result["tempo_check"]["ok"] is None
    assert result["tempo_check"]["expected_bpm"] is None
    assert result["tempo_check"]["verified"] is False


def test_preview_recognises_existing_matching_locator_without_mutation(live):
    cue = plan(tempo_bpm=120)["cues"][0]
    existing = Cue(cue["start_beats"], cue["locator_name"])
    live[0].cue_points.append(existing)
    result = invoke(live)
    assert len(result["skipped"]) == 1 and not result["would_add"]
    assert live[0].cue_points == [existing]
    assert (existing.time, existing.name) == (cue["start_beats"], cue["locator_name"])


@pytest.mark.parametrize("error_type", [ConnectionResetError, OSError, BrokenPipeError])
@pytest.mark.asyncio
async def test_preview_socket_failure_never_polls_or_retries(monkeypatch, error_type):
    from ableton_mcp.server import build_server
    from ableton_mcp.tools import film_cues
    calls = []
    class Bridge:
        async def version_info(self, **kwargs):
            return {"handlers": ["project.import_film_cues"]}
        async def call(self, op, **kwargs):
            calls.append(op)
            assert kwargs["preview"] is True
            raise error_type("preview connection failed")
    monkeypatch.setattr(film_cues, "get_bridge_client", lambda: Bridge())
    _, result = await build_server().call_tool("film_cues_import", {
        "cue_sheet": sheet(), "tempo_bpm": 120,
    })
    assert result["write_outcome"] == "not_written"
    assert calls == ["project.import_film_cues"]


@pytest.mark.asyncio
async def test_unexpected_preview_job_never_advanced(monkeypatch):
    from ableton_mcp.server import build_server
    from ableton_mcp.tools import film_cues
    calls = []
    class Bridge:
        async def version_info(self, **kwargs):
            return {"handlers": ["project.import_film_cues"]}
        async def call(self, op, **kwargs):
            calls.append(op)
            assert kwargs["preview"] is True
            return {"status": "pending", "job_id": "unexpected"}
    monkeypatch.setattr(film_cues, "get_bridge_client", lambda: Bridge())
    _, result = await build_server().call_tool("film_cues_import", {
        "cue_sheet": sheet(), "tempo_bpm": 120,
    })
    assert result["status"] == "error"
    assert result["write_outcome"] == "unknown"
    assert result["transport_restored"] is None
    assert calls == ["project.import_film_cues"]
