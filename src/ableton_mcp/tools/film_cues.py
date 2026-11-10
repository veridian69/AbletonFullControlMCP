"""Read-only scoring-to-picture interchange; automatic locator writes disabled."""
from __future__ import annotations

from typing import Any
from mcp.server.fastmcp import FastMCP
from ..bridge_client import AbletonBridgeError, get_bridge_client
from ..film_cues import plan_film_cues


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def film_cues_plan(
        cue_sheet: dict[str, Any],
        tempo_bpm: float | None = None,
        tempo_map: list[dict[str, Any]] | None = None,
        arrangement_start_beats: float = 0.0,
        cue_namespace: str = "film",
    ) -> dict[str, Any]:
        """Plan film-cues/v1 markers offline: integer relative frames -> seconds -> beats.

        Supply exactly one of tempo_bpm or tempo_map. A map is a list of
        {start_seconds, bpm}, beginning at 0, increasing strictly, with step
        tempo changes relative to picture start. start_timecode is descriptive.
        Returns {status, plan}; no Live connection, files or state changes.
        """
        try:
            return {"status": "ok", "plan": plan_film_cues(
                cue_sheet, tempo_bpm, tempo_map, arrangement_start_beats, cue_namespace,
            )}
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

    @mcp.tool()
    async def film_cues_import(
        cue_sheet: dict[str, Any],
        tempo_bpm: float | None = None,
        tempo_map: list[dict[str, Any]] | None = None,
        arrangement_start_beats: float = 0.0,
        cue_namespace: str = "film",
        preview: bool = True,
        confirm_tempo: bool = False,
    ) -> dict[str, Any]:
        """Read-only preview of film-cues/v1 markers against existing Live locators.

        preview=False is permanently refused before connecting; no override.
        Native locator toggling proved unsafe even with a negative selection query.
        Requires project.import_film_cues for preview; does not seek or create jobs.
        Tempo comparison checks only the current covered position, not automation.
        confirm_tempo is retained for compatibility, not permission to write.
        Accepts at most 500 cues. Preview proximity is not proof of safe insertion.
        """
        if preview is not True:
            return {"status": "refused", "writes_enabled": False, "write_outcome": "not_written",
                    "error": "Film locator writes disabled: native toggle can delete existing locators.",
                    "warning": "Use film_cues_plan or preview=True; no bridge connection was attempted."}
        try:
            plan = plan_film_cues(
                cue_sheet, tempo_bpm, tempo_map, arrangement_start_beats, cue_namespace,
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
        bridge = get_bridge_client()
        try:
            version = await bridge.version_info(refresh=True)
            if "project.import_film_cues" not in version.get("handlers", []):
                return {"status": "unavailable", "plan": plan, "writes_enabled": False,
                        "write_outcome": "not_written",
                        "error": "Running bridge lacks project.import_film_cues; deploy the read-only project handler."}
            result = await bridge.call(
                "project.import_film_cues", cues=plan["cues"],
                tempo_assumption=plan["tempo_assumption"],
                preview=True, confirm_tempo=confirm_tempo,
            )
            # Never poll a job, even if an older/misbehaving bridge returns one.
            if result.get("status") == "pending":
                return {"status": "error", "plan": plan, "writes_enabled": False,
                        "write_outcome": "unknown", "transport_restored": None,
                        "error": "Unexpected job from preview; no job advanced. Inspect Live."}
            return {**result, "plan": plan, "writes_enabled": False}
        except (AbletonBridgeError, OSError) as exc:
            return {"status": "error", "error": str(exc), "plan": plan,
                    "writes_enabled": False, "write_outcome": "not_written",
                    "transport_restored": None}
