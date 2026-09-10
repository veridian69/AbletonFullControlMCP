"""Pure film-cues/v1 validation and picture-seconds to musical-beats planning."""

from __future__ import annotations

import copy
import math
import re
from bisect import bisect_right
from fractions import Fraction
from typing import Any


def _object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _integer(value: Any, label: str, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError(f"{label} must be an integer" + (
            f" >= {minimum}" if minimum is not None else ""
        ))
    return value


def _number(value: Any, label: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        if not math.isfinite(value):
            raise ValueError(f"{label} must be finite")
        return Fraction(str(value))
    except OverflowError as exc:
        raise ValueError(f"{label} is too large") from exc


def _float(value: Fraction) -> float:
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError("timing value is too large") from exc
    if not math.isfinite(result):
        raise ValueError("timing value must be finite")
    return result


def plan_film_cues(
    cue_sheet: dict[str, Any],
    tempo_bpm: float | None = None,
    tempo_map: list[dict[str, Any]] | None = None,
    arrangement_start_beats: float = 0.0,
    cue_namespace: str = "film",
) -> dict[str, Any]:
    """Validate frames and integrate an explicitly supplied step-tempo map.

    The map is relative to picture start, starts at second zero, and extends
    its final tempo indefinitely. It is never inferred from Live or audio.
    """
    sheet = _object(cue_sheet, "cue_sheet")
    if sheet.get("schema") != "film-cues/v1":
        raise ValueError("cue_sheet.schema must be film-cues/v1")
    rate = _object(sheet.get("frame_rate"), "frame_rate")
    num = _integer(rate.get("numerator"), "frame_rate.numerator", 1)
    den = _integer(rate.get("denominator"), "frame_rate.denominator", 1)
    frame_seconds = Fraction(den, num)
    timeline = _object(sheet.get("timeline"), "timeline")
    _integer(timeline.get("start_frame"), "timeline.start_frame")
    for key in ("name", "start_timecode"):
        if not isinstance(timeline.get(key), str):
            raise ValueError(f"timeline.{key} must be a string")
    if not isinstance(cue_namespace, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", cue_namespace):
        raise ValueError("cue_namespace must contain 1-64 letters, digits, dots, underscores or hyphens")
    origin = _number(arrangement_start_beats, "arrangement_start_beats")
    if origin < 0:
        raise ValueError("arrangement_start_beats must be >= 0")
    if (tempo_bpm is None) == (tempo_map is None):
        raise ValueError("supply exactly one of tempo_bpm or tempo_map")
    raw_segments = [{"start_seconds": 0, "bpm": tempo_bpm}] if tempo_map is None else tempo_map
    if not isinstance(raw_segments, list) or not raw_segments or len(raw_segments) > 1000:
        raise ValueError("tempo_map must contain 1-1000 step-tempo segments")
    starts: list[Fraction] = []
    bpms: list[Fraction] = []
    beats: list[Fraction] = []
    for index, raw in enumerate(raw_segments):
        segment = _object(raw, f"tempo_map[{index}]")
        start = _number(segment.get("start_seconds"), "segment.start_seconds")
        bpm = _number(segment.get("bpm"), "segment.bpm")
        if bpm <= 0 or (not starts and start != 0) or (starts and start <= starts[-1]):
            raise ValueError("tempo_map must start at 0, increase strictly, and have positive BPM")
        beat = origin if not starts else beats[-1] + (start - starts[-1]) * bpms[-1] / 60
        starts.append(start)
        bpms.append(bpm)
        beats.append(beat)

    def at(seconds: Fraction) -> Fraction:
        i = max(0, bisect_right(starts, seconds) - 1)
        return beats[i] + (seconds - starts[i]) * bpms[i] / 60

    cues = sheet.get("cues")
    if not isinstance(cues, list) or len(cues) > 500:
        raise ValueError("cues must be an array of at most 500 markers; split larger batches")
    result = []
    identities = set()
    for index, raw in enumerate(cues):
        cue = _object(raw, f"cues[{index}]")
        start = _integer(cue.get("start_frame"), "cue.start_frame", 0)
        duration = _integer(cue.get("duration_frames"), "cue.duration_frames", 1)
        identity = cue.get("id")
        if identity != f"marker:{start}" or identity in identities:
            raise ValueError("cue IDs must be unique marker:<start_frame> strings")
        identities.add(identity)
        for key in ("name", "note", "color", "custom_data"):
            if not isinstance(cue.get(key), str):
                raise ValueError(f"cue.{key} must be a string")
        seconds = start * frame_seconds
        duration_seconds = duration * frame_seconds
        for key, expected in (("start_seconds", seconds), ("duration_seconds", duration_seconds)):
            supplied = _number(cue.get(key), f"cue.{key}")
            # Allow ordinary JSON decimal rounding, never a frame-sized discrepancy.
            if abs(supplied - expected) > min(Fraction(1, 1_000_000), frame_seconds / 1000):
                raise ValueError(f"cue.{key} disagrees with integer frames and frame_rate")
        beat = at(seconds)
        half_frame = frame_seconds / 2
        tolerance = at(seconds + half_frame) - beat
        if seconds >= half_frame:
            tolerance = min(tolerance, beat - at(seconds - half_frame))
        if _float(tolerance) <= math.ulp(_float(beat)) * 4:
            raise ValueError("cue position exceeds frame precision supported by Live beat values")
        tag = f"[film:{cue_namespace}:{identity}]"
        label = " ".join(cue["name"].split()) or identity
        if len(label) > 200:
            raise ValueError("cue.name must be at most 200 characters after whitespace normalisation")
        result.append({
            **copy.deepcopy(cue),
            "start_seconds": _float(seconds), "duration_seconds": _float(duration_seconds),
            "start_beats": _float(beat), "end_beats": _float(at(seconds + duration_seconds)),
            "locator_tag": tag, "locator_name": f"{tag} {label}",
            "tolerance_beats": _float(tolerance),
        })
    result.sort(key=lambda c: c["start_frame"])
    return {
        "source_schema": "film-cues/v1", "frame_rate": copy.deepcopy(rate),
        "timeline": copy.deepcopy(timeline), "cue_namespace": cue_namespace,
        "arrangement_start_beats": _float(origin), "cues": result,
        "tempo_assumption": {
            "mode": "constant" if tempo_map is None else "step_map",
            "verified_in_live": False,
            "segments": [{"start_seconds": _float(s), "start_beats": _float(b), "bpm": _float(t)}
                         for s, b, t in zip(starts, beats, bpms)],
        },
        "warnings": [
            "Tempo is caller-supplied. Confirm that the whole Live arrangement follows this "
            "constant tempo or step map; no automation is read, written or inferred.",
            "timeline.start_frame and start_timecode are descriptive; cue frames are relative.",
            "Locators retain an ID tag and name only; notes, colour and custom_data remain in this plan.",
        ],
    }
