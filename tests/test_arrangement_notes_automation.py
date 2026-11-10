import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
from mcp.server.fastmcp import FastMCP
from ableton_mcp.tools import arrangement

ROOT = Path(__file__).resolve().parents[1]

def handler(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'live_remote_script/AbletonFullControlBridge/handlers' / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

@pytest.mark.asyncio
async def test_arrangement_write_readback_detects_silent_failure(monkeypatch):
    class Bridge:
        async def require_handler(self, op): pass
        async def call(self, op, **kw):
            calls.append(op)
            return {'notes': []} if op.endswith('get_arrangement_notes') else {'notes_written': 1}
    calls = []
    monkeypatch.setattr(arrangement, 'get_bridge_client', Bridge)
    mcp = FastMCP('test')
    arrangement.register(mcp)
    result = await mcp.call_tool('arrangement_set_notes', {'track_index': 0, 'clip_index': 0, 'notes': [{'pitch':60,'start':0,'duration':1,'velocity':100}]})
    assert result[1]['status'] == 'error'
    assert result[1]['verified'] is False
    assert calls == ['clip.set_arrangement_notes', 'clip.get_arrangement_notes']

def test_automation_missing_api_is_unsupported_without_mutation(monkeypatch):
    mod = handler('automation')
    parameter = SimpleNamespace(min=-1, max=1, value=0, is_enabled=True)
    clip = SimpleNamespace(length=4)
    monkeypatch.setattr(mod, '_resolve', lambda **kw: (clip, parameter))
    result = mod.write_steps(None, track_index=0, clip_index=0, steps=[{'start':0,'duration':1,'value':1}])
    assert result['status'] == 'unsupported'
    assert parameter.value == 0

def test_automation_writes_and_verifies_envelope(monkeypatch):
    mod = handler('automation')
    class Envelope:
        def insert_step(self, start, duration, value): self.value = value
        def value_at_time(self, time): return self.value
    envelope = Envelope()
    clip = SimpleNamespace(length=4, automation_envelope=lambda p: envelope)
    parameter = SimpleNamespace(min=-1, max=1, value=0, is_enabled=True)
    monkeypatch.setattr(mod, '_resolve', lambda **kw: (clip, parameter))
    result = mod.write_steps(None, steps=[{'start':0,'duration':1,'value':0.5}])
    assert result['status'] == 'ok'
    assert result['verified'] is True
    assert parameter.value == 0
    with pytest.raises(ValueError):
        mod.write_steps(None, steps=[{'start':0,'duration':1,'value':2}])

def test_note_clear_failure_prevents_add(monkeypatch):
    mod = handler('clips')
    writes = []
    def fail(*args): raise RuntimeError('locked clip')
    clip = SimpleNamespace(is_midi_clip=True, length=4, remove_notes_extended=fail,
                           add_new_notes=lambda rows: writes.append(rows))
    monkeypatch.setattr(mod, '_arrangement_clip', lambda *args: clip)
    with pytest.raises(RuntimeError, match='locked clip'):
        mod.set_arrangement_notes(None, track_index=0, notes=[{'pitch':60,'start':0,'duration':1,'velocity':100}])
    assert writes == []


def test_curve_sampling_is_bounded_and_linear():
    from ableton_mcp.tools.automation import _curve_steps
    rows = _curve_steps([{'time':0,'value':-1}, {'time':1,'value':1}], 0.25)
    assert [r['value'] for r in rows] == [-.5, 0, .5, 1]
    assert sum(r['duration'] for r in rows) == 1
    with pytest.raises(ValueError):
        _curve_steps([{'time':0,'value':0}, {'time':1e9,'value':1}], .01)


def test_automation_verification_mismatch_is_error(monkeypatch):
    mod = handler('automation')
    envelope = SimpleNamespace(insert_step=lambda *a: None, value_at_time=lambda t: 0)
    clip = SimpleNamespace(automation_envelope=lambda p: envelope)
    parameter = SimpleNamespace(min=-1, max=1, value=0)
    monkeypatch.setattr(mod, '_resolve', lambda **kw: (clip, parameter))
    result = mod.write_steps(None, steps=[{'start':0,'duration':1,'value':1}])
    assert result['status'] == 'error'
    assert result['verified'] is False


@pytest.mark.asyncio
async def test_automation_tool_passes_target_and_samples(monkeypatch):
    from ableton_mcp.tools import automation
    calls = []
    class Bridge:
        async def require_handler(self, op, **kw): pass
        async def call(self, op, **kw):
            calls.append((op, kw))
            return {'status':'unsupported'}
    monkeypatch.setattr(automation, 'get_bridge_client', Bridge)
    mcp = FastMCP('test')
    automation.register(mcp)
    result = await mcp.call_tool('automation_write_curve', {'track_index':2,'clip_index':3,
        'target':'device','device_index':1,'parameter_index':5,
        'points':[{'time':0,'value':100},{'time':1,'value':1000}]})
    assert result[1]['status'] == 'unsupported'
    op, args = calls[0]
    assert op == 'automation.write_steps'
    assert args['target'] == 'device' and args['parameter_index'] == 5
    assert len(args['steps']) == 4

def test_arrangement_notes_include_content_beyond_visible_clip(monkeypatch):
    mod = handler('clips')
    calls = []
    def read(p, ps, t, span):
        calls.append(span)
        return []
    clip = SimpleNamespace(is_midi_clip=True, length=4, get_notes_extended=read,
                           remove_notes_extended=lambda *args: calls.append(args[-1]),
                           set_notes=lambda rows: None)
    monkeypatch.setattr(mod, '_arrangement_clip', lambda *a: clip)
    mod.get_arrangement_notes(None, track_index=0)
    mod.set_arrangement_notes(None, track_index=0, notes=[])
    assert min(calls) >= 1576800


def test_bridge_rejects_invalid_notes_before_removal(monkeypatch):
    mod = handler('clips')
    calls = []
    clip = SimpleNamespace(is_midi_clip=True, length=4,
                          remove_notes_extended=lambda *args: calls.append(args),
                          set_notes=lambda rows: None)
    monkeypatch.setattr(mod, '_arrangement_clip', lambda *a: clip)
    with pytest.raises(ValueError):
        mod.set_arrangement_notes(None, track_index=0,
            notes=[{'pitch':200, 'start':0, 'duration':1, 'velocity':100}])
    assert calls == []

def test_volume_target_routes_to_mixer_parameter(monkeypatch):
    import sys
    import types
    mod = handler('automation')
    # Give the isolated handler its real package shape without importing Live.
    mod.__package__ = 'automation_test_handlers'
    package = types.ModuleType('automation_test_handlers')
    clips = types.ModuleType('automation_test_handlers.clips')
    volume = SimpleNamespace(min=0, max=1, value=.8)
    clip = SimpleNamespace()
    clips._track = lambda i: SimpleNamespace(arrangement_clips=[clip], mixer_device=SimpleNamespace(volume=volume))
    monkeypatch.setitem(sys.modules, 'automation_test_handlers', package)
    monkeypatch.setitem(sys.modules, 'automation_test_handlers.clips', clips)
    resolved = mod._resolve(target='volume')
    assert resolved == (clip, volume)


def test_curve_reaches_final_target_and_rejects_nonfinite_interval():
    from ableton_mcp.tools.automation import _curve_steps
    result = _curve_steps([{'time':0,'value':1},{'time':4,'value':0}], .3)
    assert result[-1]['value'] == 0
    with pytest.raises(ValueError):
        _curve_steps([{'time':0,'value':0},{'time':1e308,'value':1}], 1e-308)
