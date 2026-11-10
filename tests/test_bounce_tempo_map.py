"""Region timing through the real wrappers; only Live capture/I/O is replaced."""
import copy

import pytest
from mcp.server.fastmcp import FastMCP

from ableton_mcp.bounce import resampling
from ableton_mcp.tools import bounce


MAP = [{"start_beats": 0, "bpm": 120}, {"start_beats": 4, "bpm": 60},
       {"start_beats": 8, "bpm": 240}]


@pytest.fixture(autouse=True)
def no_live(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("invalid input or explicit map must not contact Live here")

    monkeypatch.setattr(resampling, "get_client", forbidden)
    monkeypatch.setattr(resampling, "_read_tempo", forbidden)
    monkeypatch.setattr(resampling, "_cleanup_orphan_temp_tracks", forbidden)


@pytest.fixture
def capture(monkeypatch):
    calls = []

    async def count():
        return 3

    async def master(path, seconds, **kwargs):
        calls.append(("master", seconds, kwargs))
        return {"duration_sec": seconds, "copied": False}

    async def tracks(indices, path, seconds, **kwargs):
        calls.append((list(indices), seconds, kwargs))
        return {"duration_sec": seconds, "stems": []}

    monkeypatch.setattr(resampling, "bounce_song_via_resampling", master)
    monkeypatch.setattr(resampling, "bounce_tracks_via_resampling", tracks)
    monkeypatch.setattr(resampling, "_track_count", count)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end,seconds", [(0, 4, 2), (4, 8, 4), (2, 10, 5.5),
                                               (8, 12, 1), (3.5, 4.5, .75)])
@pytest.mark.parametrize("indices", [None, [0, 2]])
async def test_step_integration_reaches_capture_backend(tmp_path, capture, start, end, seconds, indices):
    original = copy.deepcopy(MAP)

    async def progress(value, message):
        pass

    result = await resampling.bounce_region_via_resampling(
        tmp_path, start, end, track_indices=indices, tempo_map=MAP,
        progress_callback=progress,
    )
    assert capture[0][1] == seconds
    assert capture[0][2]["start_beat"] == start
    assert capture[0][2]["progress_callback"] is progress
    assert result["region_seconds"] == seconds
    assert result["time_basis"] == "explicit_step_tempo_map"
    assert result["tempo_at_bounce"] is None
    assert result["tempo_map"] == MAP
    assert result["tempo_automation_verified"] is False
    assert MAP == original


@pytest.mark.asyncio
async def test_supported_bpm_endpoints(tmp_path, capture):
    result = await resampling.bounce_region_via_resampling(
        tmp_path, 0, 2, tempo_map=[{"start_beats": 0, "bpm": 20},
                                 {"start_beats": 1, "bpm": 999}],
    )
    assert result["region_seconds"] == pytest.approx(3.06006006006006)


@pytest.mark.asyncio
async def test_legacy_timing_is_explicitly_an_assumption(tmp_path, capture, monkeypatch):
    async def tempo():
        return 120
    monkeypatch.setattr(resampling, "_read_tempo", tempo)
    result = await resampling.bounce_region_via_resampling(tmp_path, 2, 10)
    assert capture[0][1] == 4
    assert result["time_basis"] == "assumed_current_tempo"
    assert result["tempo_at_bounce"] == 120
    assert result["tempo_map"] is None
    assert result["tempo_automation_verified"] is False


BAD_MAPS = [[], {}, [None], [{"start_beats": 1, "bpm": 120}],
            [{"start_beats": 0}], [{"start_beats": False, "bpm": 120}],
            [{"start_beats": 0, "bpm": True}], [{"start_beats": 0, "bpm": "120"}],
            [{"start_beats": 0, "bpm": 19.9}], [{"start_beats": 0, "bpm": 1000}],
            [{"start_beats": 0, "bpm": float("nan")}],
            [{"start_beats": 0, "bpm": float("inf")}],
            MAP + [{"start_beats": 8, "bpm": 100}],
            MAP + [{"start_beats": 7, "bpm": 100}],
            MAP + [{"start_beats": float("inf"), "bpm": 100}],
            MAP + [{"start_beats": 100, "bpm": 0}]]


@pytest.mark.asyncio
@pytest.mark.parametrize("tempo_map", BAD_MAPS)
@pytest.mark.parametrize("all_active", [False, True])
async def test_entire_map_validated_before_live_or_directory_creation(tmp_path, tempo_map, all_active):
    output = tmp_path / "must-not-exist"
    fn = (resampling.bounce_region_all_active_via_resampling if all_active
          else resampling.bounce_region_via_resampling)
    with pytest.raises(ValueError):
        await fn(output, 0, 1, tempo_map=tempo_map)
    assert not output.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"start_beats": float("nan")}, {"end_beats": float("inf")},
    {"start_beats": True}, {"end_beats": 0}, {"warmup_sec": -1},
    {"settle_sec": float("nan")}, {"clip_finalize_timeout_sec": 0},
    {"pre_cleanup": "false"}, {"cleanup_temp_tracks": 1},
    {"progress_callback": 123},
])
@pytest.mark.parametrize("all_active", [False, True])
async def test_invalid_region_options_precede_cleanup(tmp_path, kwargs, all_active):
    options = {"start_beats": 0, "end_beats": 4, "tempo_map": MAP, **kwargs}
    fn = (resampling.bounce_region_all_active_via_resampling if all_active
          else resampling.bounce_region_via_resampling)
    with pytest.raises(ValueError):
        await fn(tmp_path / "absent", **options)
    assert not (tmp_path / "absent").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("indices", [[-1], [True], [1.5], [0, 0], "0"])
async def test_invalid_track_indices_precede_mutation(tmp_path, indices):
    with pytest.raises(ValueError):
        await resampling.bounce_region_via_resampling(
            tmp_path / "absent", 0, 4, tempo_map=MAP, track_indices=indices,
        )
    assert not (tmp_path / "absent").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("tempo", [float("nan"), float("inf"), 0, -1])
@pytest.mark.parametrize("all_active", [False, True])
async def test_bad_current_tempo_precedes_cleanup(tmp_path, monkeypatch, tempo, all_active):
    async def read():
        return tempo
    monkeypatch.setattr(resampling, "_read_tempo", read)
    fn = (resampling.bounce_region_all_active_via_resampling if all_active
          else resampling.bounce_region_via_resampling)
    with pytest.raises(ValueError):
        await fn(tmp_path / "absent", 0, 4)
    assert not (tmp_path / "absent").exists()


def active_tracks(monkeypatch):
    class OSC:
        async def request(self, address, *args):
            replies = {"/live/song/get/num_tracks": (2,),
                       "/live/song/get/track_names": ("Piano", "Muted"),
                       "/live/track/get/mute": (args[0], args[0] == 1) if args else (),
                       "/live/track/get/has_audio_output": (args[0], True) if args else ()}
            return replies[address]

    async def get():
        return OSC()

    async def cleanup():
        return []

    async def count():
        return 2

    monkeypatch.setattr(resampling, "get_client", get)
    monkeypatch.setattr(resampling, "_cleanup_orphan_temp_tracks", cleanup)
    monkeypatch.setattr(resampling, "_track_count", count)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["bounce_region", "bounce_region_all_active"])
async def test_mcp_map_reaches_real_region_timing(tmp_path, capture, monkeypatch, name):
    active_tracks(monkeypatch)
    mcp = FastMCP("bounce-tempo-test")
    bounce.register(mcp)
    _, result = await mcp.call_tool(name, {
        "output_dir": str(tmp_path), "start_beats": 2, "end_beats": 10,
        "tempo_map": MAP, "encode_mp3": False,
    })
    assert result["status"] == "ok"
    assert capture[0][1] == 5.5
    assert result["time_basis"] == "explicit_step_tempo_map"
    if name.endswith("all_active"):
        assert capture[0][0] == [0]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["bounce_region", "bounce_region_all_active"])
async def test_bad_mcp_map_rejected_before_background_job(tmp_path, monkeypatch, name):
    def no_job(*args, **kwargs):
        pytest.fail("invalid input must not launch a background job")
    monkeypatch.setattr(bounce, "_start_background", no_job)
    mcp = FastMCP("bounce-tempo-test")
    bounce.register(mcp)
    _, result = await mcp.call_tool(name, {
        "output_dir": str(tmp_path / "absent"), "start_beats": 0, "end_beats": 4,
        "tempo_map": [{"start_beats": 1, "bpm": 120}], "background": True,
    })
    assert result["status"] == "error"
    assert not (tmp_path / "absent").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["bounce_region", "bounce_region_all_active"])
@pytest.mark.parametrize("bitrate", [0, 1000])
async def test_bad_encoding_options_rejected_before_capture(tmp_path, capture, monkeypatch, name, bitrate):
    active_tracks(monkeypatch)
    mcp = FastMCP("bounce-tempo-test")
    bounce.register(mcp)
    _, result = await mcp.call_tool(name, {
        "output_dir": str(tmp_path / "absent"), "start_beats": 0, "end_beats": 4,
        "tempo_map": MAP, "bitrate_kbps": bitrate,
    })
    assert result["status"] == "error"
    assert capture == []
    assert not (tmp_path / "absent").exists()


@pytest.mark.asyncio
async def test_unavailable_track_rejected_before_directory_or_capture(tmp_path, capture, monkeypatch):
    async def count():
        return 2
    monkeypatch.setattr(resampling, "_track_count", count)
    with pytest.raises(ValueError, match="track_index"):
        await resampling.bounce_region_via_resampling(
            tmp_path / "absent", 0, 4, tempo_map=MAP, track_indices=[2],
        )
    assert capture == []
    assert not (tmp_path / "absent").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("all_active", [False, True])
async def test_output_parent_file_rejected_before_cleanup(tmp_path, all_active):
    parent = tmp_path / "file"
    parent.write_text("existing", encoding="utf-8")
    fn = (resampling.bounce_region_all_active_via_resampling if all_active
          else resampling.bounce_region_via_resampling)
    with pytest.raises(ValueError, match="output_dir"):
        await fn(parent / "output", 0, 4, tempo_map=MAP)


@pytest.mark.asyncio
async def test_region_stems_deliver_progress_to_recording(tmp_path, monkeypatch):
    events = []

    async def names():
        return ["Piano"]

    async def input_name(index, destination_index):
        assert index == 0
        return "Piano"

    async def verify(captures):
        assert captures == [(1, "Piano", "Post Mixer")]

    async def create(name):
        return 1

    async def routing(*args):
        return True

    async def arm(*args):
        return True

    async def harvest(*args, **kwargs):
        return None

    async def record(seconds, *, start_beat, settle_sec, progress_callback, **kwargs):
        assert seconds == 5.5 and start_beat == 2 and settle_sec == .4
        await progress_callback(.5, "recording")

    async def progress(value, message):
        events.append((value, message))

    async def count():
        return 1

    for key, fn in [("_track_names", names), ("_track_count", count),
                    ("_resolve_source_input_name", input_name), ("_create_audio_track", create),
                    ("_verify_capture_setup", verify),
                    ("_set_input_routing", routing), ("_arm", arm),
                    ("_wait_for_clip_file_path", harvest), ("_record_arrangement_with_progress", record)]:
        monkeypatch.setattr(resampling, key, fn)
    result = await resampling.bounce_region_via_resampling(
        tmp_path, 2, 10, tempo_map=MAP, track_indices=[0], pre_cleanup=False,
        cleanup_temp_tracks=False, progress_callback=progress,
    )
    assert result["region_seconds"] == 5.5
    assert (.5, "recording") in events


@pytest.mark.asyncio
async def test_all_active_tool_progress_does_not_go_backwards(tmp_path, monkeypatch):
    active_tracks(monkeypatch)
    values = []

    class Registry:
        def tool(self):
            def register(fn):
                setattr(self, fn.__name__, fn)
                return fn
            return register

    class Context:
        async def report_progress(self, progress, total, **kwargs):
            values.append(progress)

    async def tracks(indices, output, seconds, **kwargs):
        await kwargs["progress_callback"](.5, "recording")
        await kwargs["progress_callback"](1., "complete")
        return {"stems": []}

    monkeypatch.setattr(resampling, "bounce_tracks_via_resampling", tracks)
    registry = Registry()
    bounce.register(registry)
    result = await registry.bounce_region_all_active(
        str(tmp_path), 2, 10, tempo_map=MAP, encode_mp3=False, ctx=Context(),
    )
    assert result["status"] == "ok"
    assert values == sorted(values)
    assert values[-1] == 1.
