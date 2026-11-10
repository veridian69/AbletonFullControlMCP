"""Read-only live verification that film-locator writes are refused.

Checks the MCP boundary and both bridge entrypoints, then compares locators and
transport. Never seeks, toggles, saves, starts playback or changes media.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ableton_mcp.bridge_client import get_bridge_client


async def run(bpm):
    root = Path(__file__).resolve().parents[1]
    sheet = {"schema": "film-cues/v1", "frame_rate": {"numerator": 24, "denominator": 1},
             "timeline": {"name": "Read-only safety verification", "start_frame": 0,
                          "start_timecode": "00:00:00:00"},
             "cues": [{"id": "marker:24", "name": "Never inserted", "note": "",
                       "color": "Blue", "custom_data": "", "start_frame": 24,
                       "duration_frames": 1, "start_seconds": 1,
                       "duration_seconds": 1 / 24}]}
    params = StdioServerParameters(command=sys.executable, args=["-m", "ableton_mcp"], cwd=str(root))
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()

            async def call(name, args=None):
                result = await session.call_tool(name, args or {})
                assert not result.isError, result
                if result.structuredContent is not None:
                    structured = result.structuredContent
                    return structured["result"] if name == "cue_points_list" else structured
                return json.loads(next(c.text for c in result.content if c.type == "text"))

            before = await call("live_get_state")
            assert not any(before[k] for k in ("is_playing", "record_mode", "session_record")), \
                "Stopped transport required for an unchanged-state comparison"
            original = await call("cue_points_list")
            result = await call("film_cues_import", {
                "cue_sheet": sheet, "tempo_bpm": bpm, "preview": False, "confirm_tempo": True,
            })
            assert result["status"] == "refused" and result["write_outcome"] == "not_written", result
            bridge = get_bridge_client()
            for op, args in (
                ("project.import_film_cues", {"preview": False, "confirm_tempo": True}),
                ("project.film_cues_step", {"job_id": "nonexistent", "cancel": False}),
            ):
                receipt = await bridge.call(op, **args)
                assert receipt["status"] == "refused" and receipt["writes_enabled"] is False, receipt
                if op == "project.import_film_cues":
                    assert receipt["write_outcome"] == "not_written", receipt
                # A retired job can have unknown historical effects. Its
                # refusal must not invent a clean historical outcome.
            remaining = await call("cue_points_list")
            after = await call("live_get_state")
            assert remaining == original
            for key in ("song_time_beats", "tempo", "is_playing", "record_mode", "session_record"):
                assert after[key] == before[key], (key, before[key], after[key])
            print(json.dumps({"status": "passed", "transport": "real MCP stdio and direct bridge",
                              "write_attempts_refused": 3, "writes": 0,
                              "locators_and_transport_unchanged": True, "set_saved": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tempo-bpm", required=True, type=float)
    args = parser.parse_args()
    asyncio.run(run(args.tempo_bpm))
