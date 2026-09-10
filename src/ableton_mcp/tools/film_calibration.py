"""Offline capture calibration tools. Main owns registration in the server."""
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..bounce import film_calibration as calibration


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def generate_calibration_probe(output_path: str, sample_rate: int = 48000,
                                   channels: int = 2, seed: int = 0) -> dict[str, Any]:
        """Create an absent 5-second PCM16 probe WAV; never overwrite or call Live.

        Parent directory must exist. Rates: 32000/44100/48000/88200/96000;
        channels: 1 or 2; seed: 0..4294967295. Four distinct landmarks per
        channel. Use only in a disposable set; retain the returned probe receipt.
        """
        try:
            return calibration.generate_calibration_probe(output_path, sample_rate, channels, seed)
        except (ValueError, OSError, RuntimeError, TypeError) as exc:
            return {"status": "error", "error": str(exc)}

    @mcp.tool()
    def measure_capture_calibration(probe: dict[str, Any], capture_paths: list[str],
                                    configuration: dict[str, Any]) -> dict[str, Any]:
        """Read-only DSP measurement of 2..5 independent full probe captures.

        Supply generate_calibration_probe's receipt and native-rate WAV paths.
        configuration requires exactly audio_path, device, driver, sample_rate,
        buffer_size_samples, settings (nonempty object describing compensation,
        routing, monitoring, plugins and other timing-relevant settings).
        Refuses weak/ambiguous/clipped/truncated audio, drift and inconsistent
        repeats. Positive offset_samples means recorded later than reference.
        Hash-bound film-calibration/v1 receipt is signal evidence, not proof of
        caller-declared settings or capture provenance. Never changes Live,
        adjusts audio, infers absolute picture origin or applies compensation.
        """
        try:
            return calibration.measure_capture_calibration(probe, capture_paths, configuration)
        except (ValueError, OSError, RuntimeError, TypeError) as exc:
            return {"status": "error", "error": str(exc)}

    @mcp.tool()
    def validate_capture_calibration(profile: dict[str, Any],
                                     configuration: dict[str, Any]) -> dict[str, Any]:
        """Recheck a film-calibration/v1 profile against CURRENT declared settings.

        Reopens all bound files, reruns DSP and verifies profile/configuration
        hashes. Changed/missing files or changed settings refuse. Read-only;
        never queries Live. Caller must truthfully provide the current settings.
        """
        try:
            return calibration.validate_capture_calibration(profile, configuration)
        except (ValueError, OSError, RuntimeError, TypeError) as exc:
            return {"status": "error", "error": str(exc)}
