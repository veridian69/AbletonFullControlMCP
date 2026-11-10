"""Compatibility rendering tools backed by Live's native resampling engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from .. import osc_client
from ..bounce import resampling
from .bounce import _run_foreground, _start_background


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def render_master(
        output_path: str,
        length_beats: float | None = None,
        tempo_map: list[dict[str, Any]] | None = None,
        background: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Record the Arrangement master from beat zero to a WAV in real time.

        Omitted length uses Live's song_length (beats). Without tempo_map,
        duration assumes the current tempo stays constant; tempo automation
        is not inferred. A map contains {start_beats, bpm} steps beginning
        at zero. Use bounce_region for a nonzero starting beat.

        Uses a temporary Resampling track, then copies its recorded file and
        removes the track. Drives Live's transport and recording state; this
        is not offline export. background=True returns a job_id to poll with
        bounce_job_status. render_clip remains an unsupported legacy tool.
        """
        async def run(progress_callback):
            try:
                if not output_path.strip() or "\x00" in output_path:
                    raise ValueError("output_path must be a non-empty WAV file path")
                out = Path(output_path)
                if out.is_dir() or out.suffix.lower() != ".wav":
                    raise ValueError("output_path must name a WAV file")
                end = length_beats
                if end is None:
                    osc = await osc_client.get_client()
                    end = float((await osc.request("/live/song/get/song_length"))[0])
                checked_map = resampling.validate_region_bounce_inputs(
                    out.parent, 0.0, end, tempo_map=tempo_map,
                )
                timing = await resampling._region_timing(0.0, end, checked_map)
                wav = await resampling.bounce_song_via_resampling(
                    output_path, timing["region_seconds"], start_beat=0.0,
                    progress_callback=progress_callback,
                )
                return {"status": "ok" if wav.get("copied") else "error",
                        "wav": wav, **timing}
            except Exception as exc:
                return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

        if background:
            return _start_background("render_master", run)

        async def progress(value, message):
            await ctx.report_progress(value, 1.0, message=message)

        return await _run_foreground("render_master", run, progress if ctx is not None else None)

    @mcp.tool()
    async def render_clip(
        track_index: int, clip_index: int, output_path: str
    ) -> dict[str, Any]:
        """[Phase 2] Bounce a single Session clip to wav."""
        return {
            "status": "not_implemented",
            "phase": 2,
            "track_index": track_index,
            "clip_index": clip_index,
            "output_path": output_path,
        }
