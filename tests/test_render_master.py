from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from ableton_mcp.tools import render


@pytest.fixture
def backend(monkeypatch):
    from ableton_mcp.bounce import resampling
    from ableton_mcp import osc_client
    osc = AsyncMock()
    osc.request.return_value = (16.0,)
    monkeypatch.setattr(osc_client, "get_client", AsyncMock(return_value=osc))
    monkeypatch.setattr(resampling, "_read_tempo", AsyncMock(return_value=120.0))
    capture = AsyncMock(return_value={"copied": True, "output_path": "out.wav"})
    monkeypatch.setattr(resampling, "bounce_song_via_resampling", capture)
    return osc, capture


async def invoke(**kwargs):
    mcp = FastMCP("render-test")
    render.register(mcp)
    result = await mcp.call_tool("render_master", {"output_path": "out.wav", **kwargs})
    return result[1] if isinstance(result, tuple) else result.structuredContent


async def test_explicit_length_captures_from_start(backend):
    osc, capture = backend
    result = await invoke(length_beats=8)
    assert result["status"] == "ok"
    assert result["time_basis"] == "assumed_current_tempo"
    assert capture.call_args.args == ("out.wav", 4.0)
    assert capture.call_args.kwargs["start_beat"] == 0.0
    osc.request.assert_not_called()


async def test_default_length_reads_song_length(backend):
    osc, capture = backend
    result = await invoke()
    assert result["status"] == "ok"
    osc.request.assert_awaited_once_with("/live/song/get/song_length")
    assert capture.call_args.args[1] == 8.0


@pytest.mark.parametrize("length", [0, -1, float("inf"), float("nan")])
async def test_invalid_length_does_not_capture(backend, length):
    result = await invoke(length_beats=length)
    assert result["status"] == "error"
    backend[1].assert_not_called()


async def test_capture_failure_is_not_success(backend):
    backend[1].return_value = {"copied": False, "error": "no recording"}
    result = await invoke(length_beats=8)
    assert result["status"] == "error"
    assert result["wav"]["error"] == "no recording"


async def test_capture_exception_is_reported(backend):
    backend[1].side_effect = RuntimeError("routing unavailable")
    result = await invoke(length_beats=8)
    assert result["status"] == "error"
    assert "routing unavailable" in result["error"]


async def test_existing_output_file_is_valid(backend, tmp_path):
    output = tmp_path / "out.wav"
    output.write_bytes(b"old capture")
    result = await invoke(output_path=str(output), length_beats=8)
    assert result["status"] == "ok"


async def test_directory_output_does_not_capture(backend, tmp_path):
    result = await invoke(output_path=str(tmp_path), length_beats=8)
    assert result["status"] == "error"
    backend[1].assert_not_called()


async def test_cancellation_propagates(backend):
    import asyncio
    backend[1].side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await invoke(length_beats=8)


@pytest.mark.parametrize("output", ["", "track.mp3"])
async def test_invalid_output_does_not_capture(backend, output):
    result = await invoke(output_path=output, length_beats=8)
    assert result["status"] == "error"
    backend[1].assert_not_called()


async def test_explicit_tempo_map(backend):
    result = await invoke(length_beats=8, tempo_map=[
        {"start_beats": 0, "bpm": 120}, {"start_beats": 4, "bpm": 60},
    ])
    assert result["status"] == "ok"
    assert backend[1].call_args.args[1] == 6.0
    assert result["time_basis"] == "explicit_step_tempo_map"


async def test_background_job_returns_result(backend):
    import asyncio
    from ableton_mcp.bounce import jobs
    jobs.reset_jobs()
    try:
        result = await invoke(length_beats=8, background=True)
        assert result["status"] == "started"
        for _ in range(10):
            await asyncio.sleep(0)
        assert backend[1].await_count == 1
        assert jobs.get_job(result["job_id"]).result["status"] == "ok"
    finally:
        jobs.reset_jobs()


async def test_foreground_refuses_active_background_job(backend):
    import asyncio
    from ableton_mcp.bounce import jobs
    gate = asyncio.Event()
    job = jobs.start_job("another-bounce", lambda progress: gate.wait())
    try:
        result = await invoke(length_beats=8)
        assert result["status"] == "busy"
        backend[1].assert_not_called()
    finally:
        await jobs.cancel_job(job.job_id)
        jobs.reset_jobs()


async def test_foreground_render_blocks_other_foreground_bounce(backend):
    import asyncio
    from ableton_mcp.tools import bounce
    gate, started = asyncio.Event(), asyncio.Event()
    async def capture(*args, **kwargs):
        started.set()
        await gate.wait()
        return {"copied": True}
    backend[1].side_effect = capture
    task = asyncio.create_task(invoke(length_beats=8))
    try:
        await started.wait()
        mcp = FastMCP("other-bounce")
        bounce.register(mcp)
        result = await mcp.call_tool("bounce_song", {
            "output_path": "other.wav", "duration_sec": 1, "encode_mp3": False,
        })
        assert result[1]["status"] == "busy"
        assert backend[1].await_count == 1
    finally:
        gate.set()
        await task
