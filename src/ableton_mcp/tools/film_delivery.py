"""Offline delivery tools; no Live transport or source-media mutations."""
from typing import Any

from mcp.server.fastmcp import FastMCP
from ..bounce.film_delivery import plan_delivery, prepare_delivery, audit_delivery


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def film_stems_prepare(
        stems: list[dict[str, Any]], output_dir: str,
        frame_rate: dict[str, int], start_frame: int, end_frame: int, tail_frames: int,
        sample_rate: int = 48000, preview: bool = True, confirm_origins: bool = False,
    ) -> dict[str, Any]:
        """Plan or create exact common-start PCM24 WAV stems plus a hash manifest.

        Each stem: {name, path, source_start_sample}. Source sample zero's
        signed position is relative to picture second zero, at sample_rate.
        Frame range is [start_frame, end_frame), plus explicit tail_frames.
        Supply rational frame_rate {numerator, denominator}, e.g. 24000/1001.
        No tempo assumption: timing is in frames/samples, not musical beats.
        preview=True reads headers only. Writes require preview=False and
        confirm_origins=True after verifying the capture origins against picture.
        Output directory must not exist; its parent must exist. Refuses wrong
        rates, insufficient audio, non-finite/clipping samples and unsafe names.
        No padding, resampling, gain, fades or dither. Last 10ms above -60dBFS
        is flagged, not altered. Does NOT verify an original capture's latency.
        """
        try:
            if preview:
                return {"status": "preview", **plan_delivery(
                    stems, frame_rate, start_frame, end_frame, tail_frames, sample_rate)}
            return prepare_delivery(stems, frame_rate, start_frame, end_frame, tail_frames,
                                    output_dir, sample_rate, confirm_origins)
        except (ValueError, OSError, RuntimeError) as exc:
            return {"status": "error", "error": str(exc)}

    @mcp.tool()
    def film_stems_audit(output_dir: str) -> dict[str, Any]:
        """Read-only audit of a film-stems/v1 delivery: WAV format, sample counts,
        sample rate, finite PCM and SHA-256. Checks package integrity, not the
        truth of caller-supplied capture origins or upstream audio latency.
        """
        try:
            return audit_delivery(output_dir)
        except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
            return {"status": "error", "error": str(exc)}
