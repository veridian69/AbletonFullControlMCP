"""Read-only saved-tempo MCP surface; main owns server registration."""
from typing import Any
from mcp.server.fastmcp import FastMCP
from ..film_tempo import read_saved_tempo


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def film_tempo_read(source_set_path: str) -> dict[str, Any]:
        """Read constant/proven-step arrangement tempo from a saved ALS, never Live.

        Returns tempo_map_beats ({start_beats,bpm}) for bounce and
        tempo_map_seconds ({start_seconds,bpm}) for film_cues_plan. Both start
        at arrangement zero. Returns source hash/version and a saved-only warning.
        Ramps, curves, enabled scene tempos, modulation and unknown topology refuse.
        Supports the inspected Live 12.3.6/12.4.5 ALS profiles; bounded read-only I/O.
        """
        try:
            return read_saved_tempo(source_set_path)
        except (ValueError, OSError) as exc:
            return {'status': 'error', 'error': str(exc), 'saved_only': True,
                    'verified_in_live': False}
