"""Elapsed-time MIDI import without changing the Live Set tempo."""
from __future__ import annotations
import math
from typing import Any
import pretty_midi


def midi_notes_at_tempo(pm: pretty_midi.PrettyMIDI, instrument_index: int,
                        destination_bpm: float) -> list[dict[str, Any]]:
    if not math.isfinite(destination_bpm) or destination_bpm <= 0:
        raise ValueError("destination_bpm must be finite and positive")
    if instrument_index < 0 or instrument_index >= len(pm.instruments):
        raise ValueError("instrument_index out of range")
    factor = destination_bpm / 60.0
    return [{"pitch": int(n.pitch), "start": float(n.start * factor),
             "duration": float((n.end - n.start) * factor),
             "velocity": int(n.velocity), "mute": False}
            for n in pm.instruments[instrument_index].notes]
