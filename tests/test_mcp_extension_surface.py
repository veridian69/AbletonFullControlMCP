from pathlib import Path
import pytest
import pretty_midi
from ableton_mcp.tools import arrangement, midi_files, high_level


class Registry:
    def __init__(self):
        self.tools = {}
    def tool(self):
        def record(function):
            self.tools[function.__name__] = function
            return function
        return record


class Bridge:
    def __init__(self):
        self.mutations = []
    async def require_handler(self, operation):
        pass
    async def call(self, operation, **args):
        self.mutations.append((operation, args))
        return {"verified": True, "clip_index": 0, "notes_imported": 1}


class OSC:
    async def request(self, operation, *args):
        assert operation == "/live/song/get/tempo"
        return (90,)


@pytest.mark.asyncio
async def test_arrangement_transfer_exposes_verified_native_bridge(monkeypatch):
    registry = Registry()
    arrangement.register(registry)
    assert "arrangement_copy_session_clip" in registry.tools
    bridge = Bridge()
    monkeypatch.setattr(arrangement, "get_bridge_client", lambda: bridge)
    result = await registry.tools["arrangement_copy_session_clip"](0, 1, 8)
    assert result["status"] == "ok"
    assert result["verified"] is True
    assert bridge.mutations == [("clip.duplicate_to_arrangement", {"track_index": 0, "slot_index": 1, "time": 8.0})]


@pytest.mark.asyncio
async def test_midi_import_refuses_destination_tempo_mismatch_before_mutation(tmp_path, monkeypatch):
    registry = Registry()
    midi_files.register(registry)
    assert "midi_file_load_into_arrangement" in registry.tools
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(0)
    inst.notes.append(pretty_midi.Note(80, 60, 1, 2))
    pm.instruments.append(inst)
    path = tmp_path / "score.mid"
    pm.write(str(path))
    bridge = Bridge()
    monkeypatch.setattr(midi_files, "get_bridge_client", lambda: bridge)
    async def client():
        return OSC()
    monkeypatch.setattr(midi_files, "get_client", client)
    result = await registry.tools["midi_file_load_into_arrangement"](str(path), 0, 8, 120)
    assert result["status"] == "error"
    assert not bridge.mutations


@pytest.mark.asyncio
async def test_path_save_routes_to_verified_ui_without_ignoring_destination(monkeypatch, tmp_path):
    registry = Registry()
    high_level.register(registry)
    assert "op_save_set_as" in registry.tools
    target = str(tmp_path / "score.als")
    async def save(path, callback):
        assert path == target
        return {"saved": True, "verified": True, "status": "ok", "path": path}
    monkeypatch.setattr(high_level, "save_set_as", save)
    result = await registry.tools["op_save_set"](target)
    assert result["saved"] is True
    assert result["path"] == target
    assert "save_as_path_ignored" not in result
