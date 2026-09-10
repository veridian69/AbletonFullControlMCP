"""Offline copy-only ALS film locator tool; no bridge dependency."""
from typing import Any
from mcp.server.fastmcp import FastMCP
from ..film_cue_set import export_film_cue_set


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def film_cues_export_set(
        source_set_path: str, output_set_path: str, cue_sheet: dict[str, Any],
        tempo_bpm: float | None = None, tempo_map: list[dict[str, Any]] | None = None,
        arrangement_start_beats: float = 0.0, cue_namespace: str = 'film',
        confirm_tempo: bool = False,
    ) -> dict[str, Any]:
        """Export film locators into a NEW offline ALS copy, never the open Live set.

        Output must be an absent .als in the source's own directory; repeats refuse.
        Requires confirm_tempo=True for the full saved-set tempo assumption.
        Supports only inspected Live 12.3.6/12.4.5 schema profiles, bounded gzip/XML.
        Preserves existing locators and all unrelated XML bytes; collisions refuse.
        Does not open/save Live, toggle locators, relocate media or alter tempo.
        Native film_cues_import writes remain permanently disabled.
        """
        try:
            return export_film_cue_set(source_set_path, output_set_path, cue_sheet,
                                       tempo_bpm, tempo_map, arrangement_start_beats,
                                       cue_namespace, confirm_tempo)
        except (ValueError, OSError) as exc:
            return {'status': 'error', 'error': str(exc), 'live_set_opened': False}
