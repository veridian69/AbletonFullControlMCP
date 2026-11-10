import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
import mido
import pretty_midi
from ableton_mcp.tools.midi_files import _midi_to_clip_notes


def handlers():
    p = Path(__file__).parents[1] / "live_remote_script/AbletonFullControlBridge/handlers/clips.py"
    spec = importlib.util.spec_from_file_location("extension_clips", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Clip:
    def __init__(self, start=0, length=4, notes=()):
        self.start_time = start
        self.length = length
        self.end_time = start + length
        self.is_midi_clip = True
        self.name = "source"
        self.notes = tuple(notes)
    def get_notes(self, *args):
        return self.notes
    def set_notes(self, notes):
        self.notes = tuple(notes)
    def remove_notes(self, *args):
        # set_arrangement_notes refuses to replace notes without a removal API.
        self.notes = ()


class Track:
    has_midi_input = True
    def __init__(self, existing=()):
        self.arrangement_clips = list(existing)
        self.clip_slots = [SimpleNamespace(has_clip=True, clip=Clip())]
    def duplicate_clip_to_arrangement(self, clip, time):
        assert isinstance(clip, Clip)
        copied = Clip(time, clip.length, clip.notes)
        self.arrangement_clips.append(copied)
        return copied


def test_transfer_refuses_overlap_before_any_clip_is_created(monkeypatch):
    mod = handlers()
    track = Track([Clip(2, 4)])
    monkeypatch.setattr(mod, "_track", lambda i: track)
    with pytest.raises(ValueError, match="overlap"):
        mod.duplicate_to_arrangement(None, track_index=0, slot_index=0, time=4)
    assert len(track.arrangement_clips) == 1


def test_transfer_verifies_native_clip_and_allows_abutting_boundary(monkeypatch):
    mod = handlers()
    track = Track([Clip(0, 4)])
    monkeypatch.setattr(mod, "_track", lambda i: track)
    result = mod.duplicate_to_arrangement(None, track_index=0, slot_index=0, time=4)
    assert result["verified"] is True
    assert result["clip_index"] == 1
    assert track.arrangement_clips[1].start_time == 4


def test_transfer_never_reports_success_for_native_noop(monkeypatch):
    mod = handlers()
    track = Track()
    track.duplicate_clip_to_arrangement = lambda clip, time: None
    monkeypatch.setattr(mod, "_track", lambda i: track)
    with pytest.raises(RuntimeError, match="verif"):
        mod.duplicate_to_arrangement(None, track_index=0, slot_index=0, time=4)
    assert not track.arrangement_clips


@pytest.mark.parametrize("position", [-1, float("nan"), float("inf")])
def test_transfer_rejects_invalid_positions_without_mutating(monkeypatch, position):
    mod = handlers()
    track = Track()
    monkeypatch.setattr(mod, "_track", lambda i: track)
    with pytest.raises(ValueError):
        mod.duplicate_to_arrangement(None, track_index=0, slot_index=0, time=position)
    assert not track.arrangement_clips


def tempo_file(tmp_path):
    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr.extend([mido.MetaMessage("set_tempo", tempo=500000),
               mido.MetaMessage("set_tempo", tempo=1000000, time=960),
               mido.Message("note_on", note=60, velocity=90, time=480),
               mido.Message("note_off", note=60, velocity=0, time=480)])
    p = tmp_path / "tempo.mid"
    mid.save(p)
    return pretty_midi.PrettyMIDI(str(p))


def test_source_beat_mapping_integrates_all_tempo_segments(tmp_path):
    notes = _midi_to_clip_notes(tempo_file(tmp_path))
    assert notes[0]["start"] == pytest.approx(3)
    assert notes[0]["duration"] == pytest.approx(1)


def test_elapsed_timing_mapping_at_explicit_destination_tempo(tmp_path):
    from ableton_mcp.arrangement_import import midi_notes_at_tempo
    notes = midi_notes_at_tempo(tempo_file(tmp_path), 0, 90)
    assert notes[0]["start"] == pytest.approx(3)
    assert notes[0]["duration"] == pytest.approx(1.5)


class Slot:
    def __init__(self):
        self.clip = None
    @property
    def has_clip(self):
        return self.clip is not None
    def create_clip(self, length):
        self.clip = Clip(length=length)
    def delete_clip(self):
        self.clip = None


def test_import_stages_only_empty_slot_verifies_notes_and_cleans_up(monkeypatch):
    mod = handlers()
    track = Track()
    track.clip_slots.append(Slot())
    monkeypatch.setattr(mod, "_track", lambda i: track)
    notes = [{"pitch": 64, "start": 1.5, "duration": 2.0, "velocity": 80, "mute": False}]
    result = mod.import_midi_to_arrangement(None, track_index=0, time=8, length=4, notes=notes)
    assert result["verified"] is True
    assert track.clip_slots[0].has_clip
    assert not track.clip_slots[1].has_clip
    assert track.arrangement_clips[0].notes == ((64, 1.5, 2.0, 80, False),)


def test_import_full_session_refuses_without_changing_existing_clips(monkeypatch):
    mod = handlers()
    track = Track()
    monkeypatch.setattr(mod, "_track", lambda i: track)
    with pytest.raises(ValueError, match="empty"):
        mod.import_midi_to_arrangement(None, track_index=0, time=8, length=4,
            notes=[{"pitch": 64, "start": 0, "duration": 2, "velocity": 80}])
    assert not track.arrangement_clips
    assert track.clip_slots[0].has_clip


def test_import_rejects_audio_track_before_staging(monkeypatch):
    mod = handlers()
    track = Track()
    track.has_midi_input = False
    track.clip_slots.append(Slot())
    monkeypatch.setattr(mod, "_track", lambda i: track)
    with pytest.raises(ValueError, match="MIDI"):
        mod.import_midi_to_arrangement(None, track_index=0, time=8, length=4,
            notes=[{"pitch": 64, "start": 0, "duration": 2, "velocity": 80}])
    assert not track.clip_slots[1].has_clip


def test_import_uses_native_arrangement_creation_without_requiring_empty_session(monkeypatch):
    mod = handlers()
    track = Track()
    def create(start, length):
        clip = Clip(start, length)
        track.arrangement_clips.append(clip)
        return clip
    track.create_midi_clip = create
    monkeypatch.setattr(mod, "_track", lambda i: track)
    notes = [{"pitch": 64, "start": 1, "duration": 2, "velocity": 80}]
    result = mod.import_midi_to_arrangement(None, track_index=0, time=8, length=4, notes=notes)
    assert result["verified"] is True
    assert track.arrangement_clips[0].notes == ((64, 1.0, 2.0, 80, False),)
    assert track.clip_slots[0].has_clip


def test_import_reports_note_loss_as_verification_failed(monkeypatch):
    mod = handlers()
    track = Track()
    def create(start, length):
        clip = Clip(start, length)
        clip.set_notes = lambda notes: setattr(clip, "notes", tuple(notes)[:1])
        track.arrangement_clips.append(clip)
    track.create_midi_clip = create
    monkeypatch.setattr(mod, "_track", lambda i: track)
    notes = [{"pitch": 64, "start": 0, "duration": 1, "velocity": 80},
             {"pitch": 66, "start": 2, "duration": 1, "velocity": 80}]
    result = mod.import_midi_to_arrangement(None, track_index=0, time=8, length=4, notes=notes)
    assert result["status"] == "verification_failed"
    assert result["verified"] is False
    assert result["notes_expected"] == 2
    assert result["notes_imported"] == 1


def test_existing_arrangement_creation_uses_start_and_length_not_end(monkeypatch):
    mod = handlers()
    track = Track()
    def create(start, length):
        clip = Clip(start, length)
        track.arrangement_clips.append(clip)
        return clip
    track.create_midi_clip = create
    monkeypatch.setattr(mod, "_track", lambda i: track)
    result = mod.create_arrangement_midi_clip(None, track_index=0, position=16, length=4)
    assert track.arrangement_clips[0].end_time == 20
    assert result["length"] == 4
    assert result["verified"] is True
