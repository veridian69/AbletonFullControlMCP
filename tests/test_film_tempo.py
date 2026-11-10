"""Saved tempo contracts: actual XML topology, never a Live connection."""
import gzip
import hashlib
import xml.etree.ElementTree as ET

import pytest
from mcp.server.fastmcp import FastMCP


def document(events=None):
    if events is None:
        events = [('-63072000', '120')]
    points = ''.join(f'<FloatEvent Id="{i}" Time="{t}" Value="{b}" />'
                     for i, (t, b) in enumerate(events))
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="12.0_12402" SchemaChangeCount="5" Creator="Ableton Live 12.4.5">
<LiveSet><Tracks/><MainTrack><AutomationEnvelopes><Envelopes>
<AutomationEnvelope Id="1"><EnvelopeTarget><PointeeId Value="8"/></EnvelopeTarget>
<Automation><Events>{points}</Events><AutomationTransformViewState>
<IsTransformPending Value="false"/><TimeAndValueTransforms/>
</AutomationTransformViewState></Automation></AutomationEnvelope>
</Envelopes></AutomationEnvelopes><DeviceChain><Mixer><Tempo>
<LomId Value="0"/><Manual Value="120"/><MidiControllerRange><Min Value="60"/><Max Value="200"/></MidiControllerRange>
<AutomationTarget Id="8"><LockEnvelope Value="0"/></AutomationTarget>
<ModulationTarget Id="9"><LockEnvelope Value="0"/></ModulationTarget>
</Tempo><UserTempoAutomation><Value/></UserTempoAutomation></Mixer></DeviceChain></MainTrack>
<Locators><Locators/></Locators><Scenes><Scene Id="0"><Tempo Value="120"/><IsTempoEnabled Value="false"/></Scene></Scenes>
<SignalModulations/></LiveSet></Ableton>'''.encode()


def saved(tmp_path, xml=None):
    path = tmp_path / 'score.als'
    path.write_bytes(gzip.compress(document() if xml is None else xml, mtime=42))
    return path


def read(path):
    from ableton_mcp.film_tempo import read_saved_tempo
    return read_saved_tempo(str(path))


def test_constant_120_has_both_consumer_maps_and_immutable_provenance(tmp_path):
    path = saved(tmp_path)
    before = path.read_bytes()
    result = read(path)
    assert result['status'] == 'ok' and result['schema'] == 'film-tempo/v1'
    assert result['mode'] == 'constant' and result['tempo_bpm'] == 120
    assert result['tempo_map_beats'] == [{'start_beats': 0, 'bpm': 120}]
    assert result['tempo_map_seconds'] == [{'start_seconds': 0, 'bpm': 120}]
    assert result['source_sha256'] == hashlib.sha256(before).hexdigest()
    assert result['source_version']['Creator'] == 'Ableton Live 12.4.5'
    assert result['saved_only'] is True and result['verified_in_live'] is False
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('initial', ['-63072000', '0'])
def test_same_time_breakpoint_steps_integrate_without_ramps(tmp_path, initial):
    path = saved(tmp_path, document([(initial, '120'), ('4', '120'), ('4', '60'),
                                     ('8', '60'), ('8', '240'), ('12', '240')]))
    result = read(path)
    assert result['mode'] == 'step_map' and result['tempo_bpm'] is None
    assert result['tempo_map_beats'] == [
        {'start_beats': 0, 'bpm': 120}, {'start_beats': 4, 'bpm': 60}, {'start_beats': 8, 'bpm': 240}]
    assert result['tempo_map_seconds'] == [
        {'start_seconds': 0, 'bpm': 120}, {'start_seconds': 2, 'bpm': 60}, {'start_seconds': 6, 'bpm': 240}]
    # Exercise the real cue planner with extracted seconds rather than mirror its maths.
    from ableton_mcp.film_cues import plan_film_cues
    from tests.test_film_cues import sheet
    cue = plan_film_cues(sheet((168,)), tempo_map=result['tempo_map_seconds'])['cues'][0]
    assert cue['start_beats'] == 12
    from ableton_mcp.bounce.resampling import _step_region_seconds
    assert _step_region_seconds(2, 10, result['tempo_map_beats']) == 5.5


def test_zero_time_step_replaces_sentinel_and_ignores_manual_playhead_value(tmp_path):
    result = read(saved(tmp_path, document([('-63072000', '60'), ('0', '60'), ('0', '90')])))
    assert result['tempo_map_beats'] == [{'start_beats': 0, 'bpm': 90}]
    assert result['manual_bpm'] == 120


@pytest.mark.parametrize('events', [
    [], [('4', '120')], [('-1', '120')],
    [('-63072000', '120'), ('4', '90')],  # ramp, NOT a step
    [('0', '120'), ('4', '120'), ('4.000001', '90')],
    [('0', '120'), ('8', '120'), ('4', '120')],
    [('0', '120'), ('4', '120'), ('4', '90'), ('4', '60')],
    [('0', '0')], [('0', 'nan')], [('0', 'inf')], [('0', '1000')],
    [('-63072000', '120'), ('-63072000', '90')],
])
def test_invalid_or_non_step_events_refuse_without_approximation(tmp_path, events):
    path = saved(tmp_path, document(events))
    with pytest.raises(ValueError):
        read(path)


@pytest.mark.parametrize('mutation', [
    lambda r: r.find('.//Tempo').remove(r.find('.//Tempo/AutomationTarget')),
    lambda r: r.find('.//Tempo').append(ET.fromstring('<AutomationTarget Id="8"><LockEnvelope Value="0"/></AutomationTarget>')),
    lambda r: r.find('.//Tracks').append(ET.fromstring('<AutomationTarget Id="8"/>')),
    lambda r: r.find('.//Envelopes').remove(r.find('.//AutomationEnvelope')),
    lambda r: r.find('.//Envelopes').append(ET.fromstring(ET.tostring(r.find('.//AutomationEnvelope')))),
    lambda r: r.find('.//PointeeId').set('Value', '123'),
    lambda r: r.find('.//FloatEvent').set('CurveControl1X', '0.5'),
    lambda r: r.find('.//FloatEvent').set('FutureInterpolation', 'step'),
    lambda r: r.find('.//FloatEvent').set('Id', '-1'),
    lambda r: r.find('.//IsTransformPending').set('Value', 'true'),
    lambda r: r.find('.//TimeAndValueTransforms').append(ET.Element('Transform')),
    lambda r: r.find('.//IsTempoEnabled').set('Value', 'true'),
    lambda r: r.find('.//Scene').remove(r.find('.//IsTempoEnabled')),
    lambda r: r.find('.//SignalModulations').append(ET.fromstring('<Modulation Target="9"/>')),
    lambda r: r.find('.//Tracks').append(ET.fromstring('<EnvelopeTarget><PointeeId Value="9"/></EnvelopeTarget>')),
    lambda r: r.find('.//UserTempoAutomation/Value').set('Value', '120'),
    lambda r: r.find('.//LiveSet').append(ET.fromstring('<TempoFollowerEnabled Value="true"/>')),
    lambda r: r.find('.//Tempo').append(ET.fromstring('<UnknownTempoSource/>')),
    lambda r: r.find('.//AutomationEnvelope').set('Disabled', 'true'),
    lambda r: r.find('.//EnvelopeTarget').append(ET.fromstring('<AlternateSource Value="99"/>')),
    lambda r: r.find('.//Events').set('Interpolation', 'hold'),
    lambda r: setattr(r.find('.//FloatEvent'), 'text', 'unknown event data'),
])
def test_ambiguous_targets_curves_scene_overrides_and_modulation_refuse(tmp_path, mutation):
    root = ET.fromstring(document())
    mutation(root)
    path = saved(tmp_path, ET.tostring(root))
    with pytest.raises(ValueError):
        read(path)


@pytest.mark.parametrize('data', [b'plain', gzip.compress(document())[:-4], gzip.compress(b'<broken>'),
    gzip.compress(document().replace(b'<Ableton ', b'<!DOCTYPE Ableton><Ableton ')),
    gzip.compress(document().replace(b'12.0_12402', b'99.0_1'))])
def test_malformed_gzip_xml_or_unknown_version_refuses(tmp_path, data):
    path = tmp_path / 'bad.als'
    path.write_bytes(data)
    with pytest.raises(ValueError):
        read(path)


@pytest.mark.asyncio
async def test_standalone_mcp_registration_and_error_contract(tmp_path, monkeypatch):
    from ableton_mcp.tools import film_tempo
    from ableton_mcp import bridge_client
    monkeypatch.setattr(bridge_client, 'get_bridge_client', lambda: pytest.fail('read-only tool contacted Live'))
    server = FastMCP('saved-tempo-test')
    film_tempo.register(server)
    _, result = await server.call_tool('film_tempo_read', {'source_set_path': str(saved(tmp_path))})
    assert result['tempo_bpm'] == 120 and result['status'] == 'ok'
    _, failure = await server.call_tool('film_tempo_read', {'source_set_path': str(tmp_path / 'missing.als')})
    assert failure['status'] == 'error'
    assert failure['verified_in_live'] is False
    assert 'tempo_map_beats' not in failure


@pytest.mark.parametrize('limit_name', ['MAX_COMPRESSED_BYTES', 'MAX_XML_BYTES'])
def test_shared_als_size_bounds_are_enforced(tmp_path, monkeypatch, limit_name):
    from ableton_mcp import film_cue_set
    path = saved(tmp_path)
    monkeypatch.setattr(film_cue_set, limit_name, 128)
    with pytest.raises(ValueError, match='limit'):
        read(path)


def test_sub_float_precision_step_times_are_not_merged(tmp_path):
    path = saved(tmp_path, document([('0', '120'), ('1', '120'), ('1', '90'),
        ('1.00000000000000001', '90'), ('1.00000000000000001', '60')]))
    with pytest.raises(ValueError, match='precision'):
        read(path)


def test_manual_without_tempo_envelope_is_not_silently_certified(tmp_path):
    root = ET.fromstring(document())
    root.find('.//Envelopes').clear()
    with pytest.raises(ValueError, match='tempo envelope'):
        read(saved(tmp_path, ET.tostring(root)))


def test_target_resolution_is_not_hardcoded_to_ch5_ids(tmp_path):
    xml = document().replace(b'Id="8"', b'Id="1234"').replace(b'Value="8"', b'Value="1234"')
    xml = xml.replace(b'Id="9"', b'Id="1235"')
    result = read(saved(tmp_path, xml))
    assert result['automation_target_id'] == '1234'
    assert result['tempo_bpm'] == 120


def test_hash_binds_changed_saved_source_not_just_path(tmp_path):
    path = saved(tmp_path)
    first = read(path)
    path.write_bytes(gzip.compress(document([('-63072000', '90')]), mtime=42))
    second = read(path)
    assert first['source_set_path'] == second['source_set_path']
    assert first['source_sha256'] != second['source_sha256']
    assert second['source_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert second['tempo_bpm'] == 90


@pytest.mark.parametrize('kind,alias', [
    ('automation_reference', '08'), ('automation_reference', '+8'),
    ('modulation_reference', '09'), ('modulation_reference', '+9'),
    ('duplicate_definition', '08'), ('duplicate_definition', '+8'),
    ('modulation_definition', '09'), ('modulation_definition', '+9'),
])
def test_numeric_aliases_cannot_bypass_tempo_source_validation(tmp_path, kind, alias):
    root = ET.fromstring(document())
    if kind.endswith('reference'):
        extra = ET.fromstring(f'<EnvelopeTarget><PointeeId Value="{alias}"/></EnvelopeTarget>')
    else:
        tag = 'ModulationTarget' if kind == 'modulation_definition' else 'AutomationTarget'
        extra = ET.fromstring(f'<{tag} Id="{alias}"><LockEnvelope Value="0"/></{tag}>')
    # Outside the main tempo envelope: raw-string filtering used to skip these.
    root.find('LiveSet/Tracks').append(extra)
    with pytest.raises(ValueError, match='ID'):
        read(saved(tmp_path, ET.tostring(root)))
