"""Persistent clip-envelope tools; runtime capabilities vary with Live."""
from __future__ import annotations
import math
from typing import Any
from mcp.server.fastmcp import FastMCP
from ..bridge_client import get_bridge_client


def _curve_steps(points: list[dict[str, float]], resolution_beats: float) -> list[dict[str, float]]:
    if not math.isfinite(resolution_beats) or resolution_beats <= 0:
        raise ValueError('resolution_beats must be positive and finite')
    if not 2 <= len(points) <= 4097:
        raise ValueError('provide 2..4097 points with time and value')
    rows = [(float(p['time']), float(p['value'])) for p in points]
    if any(not math.isfinite(v) for row in rows for v in row) or rows[0][0] < 0:
        raise ValueError('points must be finite and times non-negative')
    result = []
    for (start, v0), (end, v1) in zip(rows, rows[1:]):
        if end <= start or not math.isfinite(end - start):
            raise ValueError('point times must strictly increase')
        ratio = (end - start) / resolution_beats
        if not math.isfinite(ratio) or ratio > 4096:
            raise ValueError('curve exceeds 4096 steps; increase resolution_beats')
        count = math.ceil(ratio)
        if len(result) + count > 4096:
            raise ValueError('curve exceeds 4096 steps; increase resolution_beats')
        duration = (end - start) / count
        for i in range(count):
            result.append({'start': start + i * duration, 'duration': duration,
                           'value': v0 * (1 - (i + 1) / count) + v1 * ((i + 1) / count)})
    return result


async def _call(op: str, **kwargs: Any) -> dict[str, Any]:
    try:
        bridge = get_bridge_client()
        await bridge.require_handler('automation.' + op)
        return await bridge.call('automation.' + op, **kwargs)
    except Exception as exc:
        return {'status': 'error', 'error': str(exc)}


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def automation_capabilities(track_index: int, clip_index: int, view: str = 'arrangement',
                                      target: str = 'pan', device_index: int = 0,
                                      parameter_index: int = 0, send_index: int = 0) -> dict[str, Any]:
        """Read-only Python clip-envelope capability probe for pan, volume, send or device.

        Device target addresses a top-level device parameter (e.g. filter cutoff).
        Values use native parameter units. This is clip automation, not a global
        Arrangement track automation lane. Indices are zero-based; Session view
        uses clip_index as slot index. Probe does not create an envelope.
        """
        return await _call('capabilities', **locals())

    @mcp.tool()
    async def automation_write_curve(track_index: int, clip_index: int,
                                     points: list[dict[str, float]], resolution_beats: float = 0.25,
                                     view: str = 'arrangement', target: str = 'pan',
                                     device_index: int = 0, parameter_index: int = 0,
                                     send_index: int = 0) -> dict[str, Any]:
        """Persist a clip envelope from {time, value} points in clip-local beats.

        Piecewise-linear curves are approximated by right-edge sampled steps at
        resolution_beats or finer, maximum 4096. Each interval uses its right-edge value, reaching the final target in
        the final step (up to one step early). Pan/volume/send/
        device values use native ranges from automation_capabilities. Inserts
        only these intervals, preserves other automation, checks midpoint samples.
        Does not set parameter.value. Unsupported Python APIs return unsupported.
        Changes persist in the Live set when saved; no automatic save is performed.
        """
        args = dict(locals())
        try:
            steps = _curve_steps(points, resolution_beats)
        except (ValueError, KeyError, TypeError) as exc:
            return {'status': 'error', 'error': str(exc)}
        del args['points'], args['resolution_beats']
        result = await _call('write_steps', steps=steps, **args)
        result['curve_interpolation'] = 'piecewise-linear approximated by right-edge sampled steps'
        return result

    @mcp.tool()
    async def automation_read_values(track_index: int, clip_index: int, times: list[float],
                                     view: str = 'arrangement', target: str = 'pan',
                                     device_index: int = 0, parameter_index: int = 0,
                                     send_index: int = 0) -> dict[str, Any]:
        """Sample an existing clip envelope at clip-local beat times; does not read parameter.value."""
        return await _call('read_values', **locals())
