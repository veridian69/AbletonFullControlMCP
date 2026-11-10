"""Fail-closed extraction of saved arrangement tempo; no Live access or writes."""
from __future__ import annotations

from fractions import Fraction
import gzip
import hashlib
import io
import math
from pathlib import Path
import re
import stat
from typing import Any
import xml.etree.ElementTree as ET
import zlib

from . import film_cue_set as als

_SENTINEL = Fraction(-63072000)
_MAX_EVENTS = 10000
_MAX_SEGMENTS = 1000


def _one(parent: ET.Element, path: str) -> ET.Element:
    items = parent.findall(path)
    if len(items) != 1:
        raise ValueError(f'Expected exactly one {path}; saved tempo topology is ambiguous')
    return items[0]


def _value(node: ET.Element) -> str:
    if set(node.attrib) != {'Value'} or len(node) or (node.text or '').strip():
        raise ValueError(f'Unsupported {node.tag} value structure')
    return node.get('Value')


def _number(text: str, label: str) -> Fraction:
    if (not isinstance(text, str) or len(text) > 80
            or not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?', text)):
        raise ValueError(f'{label} must be a bounded finite numeric value')
    value = Fraction(text)
    if abs(value) > 2**53:
        raise ValueError(f'{label} exceeds interoperable precision')
    return value


def _bpm(text: str) -> Fraction:
    bpm = _number(text, 'BPM')
    if not 20 <= bpm <= 999:
        raise ValueError('BPM must be between 20 and 999 for bounce compatibility')
    return bpm


def _id(text: str) -> int:
    if not isinstance(text, str) or not re.fullmatch(r'0|[1-9][0-9]{0,9}', text):
        raise ValueError('Invalid tempo target/event ID')
    value = int(text)
    if value >= 2**31:
        raise ValueError('Tempo target/event ID out of range')
    return value


def _read_xml(source_set_path: str) -> tuple[Path, bytes, ET.Element]:
    if not isinstance(source_set_path, str):
        raise ValueError('source_set_path must be an absolute .als path')
    path = Path(source_set_path)
    if not path.is_absolute() or path.suffix.lower() != '.als' or ':' in path.name:
        raise ValueError('source_set_path must be an absolute .als path')
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Symlink/reparse paths are unsupported')
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Source must be a regular ALS file')
    raw = als._read(path)
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            xml = stream.read(als.MAX_XML_BYTES + 1)
    except (OSError, EOFError, zlib.error) as exc:
        raise ValueError('Invalid gzip ALS') from exc
    # Reuse the bounded UTF-8, depth, DTD, version and LiveSet topology validator.
    # This is an inspection helper only; no copy/export function is invoked.
    als._inspect(xml)
    return path.resolve(), raw, ET.fromstring(xml)


def _tempo_envelope(root: ET.Element) -> tuple[ET.Element, Fraction, str]:
    live = _one(root, 'LiveSet')
    main = _one(live, 'MainTrack')
    mixer = _one(main, 'DeviceChain/Mixer')
    tempo = _one(mixer, 'Tempo')
    expected = ['LomId', 'Manual', 'MidiControllerRange', 'AutomationTarget', 'ModulationTarget']
    if tempo.attrib or [c.tag for c in tempo] != expected:
        raise ValueError('Unsupported tempo control topology')
    _value(_one(tempo, 'LomId'))
    manual = _bpm(_value(_one(tempo, 'Manual')))
    targets = [_one(tempo, name) for name in ('AutomationTarget', 'ModulationTarget')]
    identities = []
    for target in targets:
        if set(target.attrib) != {'Id'} or [c.tag for c in target] != ['LockEnvelope']:
            raise ValueError('Unsupported tempo target topology')
        identity = _id(target.get('Id'))
        if _value(target[0]) != '0':
            raise ValueError('Unknown tempo envelope lock state')
        identities.append(identity)
    if identities[0] == identities[1]:
        raise ValueError('Duplicate tempo target identity')
    definitions = {}
    for element in root.iter():
        if element.tag in ('AutomationTarget', 'ModulationTarget'):
            # Validate BEFORE filtering. Raw spellings such as 08/+8 must not
            # hide another definition of the selected numeric target.
            identity = _id(element.get('Id'))
            definitions.setdefault(identity, []).append(element)
    for identity in identities:
        if len(definitions.get(identity, [])) != 1:
            raise ValueError('Missing or duplicate global tempo target definition')

    user = _one(mixer, 'UserTempoAutomation')
    if user.attrib or len(user) != 1 or user[0].tag != 'Value' or user[0].attrib or len(user[0]):
        raise ValueError('User tempo automation is unsupported')
    if (user.text or '').strip() or (user[0].text or '').strip():
        raise ValueError('Unknown user tempo automation data')
    scenes = _one(live, 'Scenes')
    for scene in scenes:
        if scene.tag != 'Scene' or _value(_one(scene, 'IsTempoEnabled')) != 'false':
            raise ValueError('Scene tempo override or unknown scene topology')
        _bpm(_value(_one(scene, 'Tempo')))
    for modulations in root.iter('SignalModulations'):
        if len(modulations) or modulations.attrib or (modulations.text or '').strip():
            raise ValueError('Signal modulation is unsupported; saved tempo cannot be certified')

    # Do not silently ignore a new tempo source elsewhere in the saved schema.
    allowed_paths = {
        'Ableton/LiveSet/MainTrack/DeviceChain/Mixer/Tempo',
        'Ableton/LiveSet/MainTrack/DeviceChain/Mixer/UserTempoAutomation',
        'Ableton/LiveSet/MainTrack/DeviceChain/Mixer/TempoAutomationViewBottom',
        'Ableton/LiveSet/MainTrack/DeviceChain/Mixer/TempoAutomationViewTop',
        'Ableton/LiveSet/Scenes/Scene/Tempo', 'Ableton/LiveSet/Scenes/Scene/IsTempoEnabled',
        'Ableton/LiveSet/Transport/PhaseNudgeTempo',
        'Ableton/LiveSet/ShouldSceneTempoAndTimeSignatureBeVisible',
    }
    def check_paths(node, prefix=''):
        path = prefix + node.tag
        if 'tempo' in node.tag.lower() and path not in allowed_paths:
            raise ValueError(f'Unknown tempo topology: {path}')
        for child in node:
            check_paths(child, path + '/')
    check_paths(root)

    envelopes = _one(main, 'AutomationEnvelopes/Envelopes')
    matches = []
    for envelope in envelopes:
        if envelope.tag != 'AutomationEnvelope':
            raise ValueError('Unknown arrangement envelope topology')
        pointee = _one(envelope, 'EnvelopeTarget/PointeeId')
        identity = _id(_value(pointee))
        if identity == identities[0]:
            matches.append((envelope, pointee))
    if len(matches) != 1:
        raise ValueError('Missing or duplicate arrangement tempo envelope')
    envelope, pointee = matches[0]
    references = []
    for element in root.iter('PointeeId'):
        # This includes references outside the main arrangement envelope.
        identity = _id(_value(element))
        if identity in identities:
            references.append(element)
    if references != [pointee]:
        raise ValueError('Additional tempo automation/modulation references are unsupported')
    if (set(envelope.attrib) != {'Id'}
            or [c.tag for c in envelope] != ['EnvelopeTarget', 'Automation']):
        raise ValueError('Unknown tempo envelope fields')
    _id(envelope.get('Id'))
    envelope_target = _one(envelope, 'EnvelopeTarget')
    if envelope_target.attrib or [c.tag for c in envelope_target] != ['PointeeId']:
        raise ValueError('Unknown tempo envelope target fields')
    automation = _one(envelope, 'Automation')
    if automation.attrib or [c.tag for c in automation] != ['Events', 'AutomationTransformViewState']:
        raise ValueError('Unknown tempo automation fields')
    transform = _one(automation, 'AutomationTransformViewState')
    if (transform.attrib or [c.tag for c in transform] != ['IsTransformPending', 'TimeAndValueTransforms']
            or _value(transform[0]) != 'false' or transform[1].attrib or len(transform[1])
            or (transform[1].text or '').strip()):
        raise ValueError('Pending or unknown automation transforms cannot be extracted')
    return _one(automation, 'Events'), manual, str(identities[0])


def _steps(events: ET.Element) -> list[tuple[Fraction, Fraction]]:
    if events.attrib or not 1 <= len(events) <= _MAX_EVENTS:
        raise ValueError('Tempo envelope must contain 1-10000 events')
    points, ids = [], set()
    for event in events:
        if (event.tag != 'FloatEvent' or set(event.attrib) != {'Id', 'Time', 'Value'}
                or len(event) or (event.text or '').strip()):
            raise ValueError('Curves or unknown tempo interpolation fields are unsupported')
        identity = _id(event.get('Id'))
        if identity in ids:
            raise ValueError('Duplicate tempo event ID')
        ids.add(identity)
        time = _number(event.get('Time'), 'event time')
        bpm = _bpm(event.get('Value'))
        if not points:
            if time not in (_SENTINEL, 0):
                raise ValueError('Tempo must start at the known initial sentinel or arrangement zero')
        elif time < 0 or time < points[-1][0]:
            raise ValueError('Negative, duplicate sentinel or unordered tempo event')
        points.append((time, bpm))

    steps = [(Fraction(0), points[0][1])]
    same_time_count = 1
    for (left, old), (right, new) in zip(points, points[1:]):
        if right > left:
            same_time_count = 1
            if new != old:
                raise ValueError('Positive-duration BPM change is a ramp, not a proven step')
        else:
            same_time_count += 1
            if same_time_count > 2:
                raise ValueError('Ambiguous multiple tempo breakpoints at one time')
            if new != old:
                if steps[-1][0] == right:
                    steps[-1] = (right, new)
                else:
                    steps.append((right, new))
    if len(steps) > _MAX_SEGMENTS:
        raise ValueError('Tempo map exceeds the 1000-segment cue-planner limit')
    return steps


def read_saved_tempo(source_set_path: str) -> dict[str, Any]:
    """Extract proven constant/step arrangement tempo from a bounded saved ALS.

    Maps begin at arrangement beat/second zero and continue their final tempo.
    Scene overrides, modulation, ramps and unknown topology refuse. No Live
    access, filesystem writes or inference from the current playhead occurs.
    """
    path, raw, root = _read_xml(source_set_path)
    events, manual, target = _tempo_envelope(root)
    steps = _steps(events)
    beat_map, seconds_map = [], []
    seconds = Fraction(0)
    for i, (beat, bpm) in enumerate(steps):
        if i:
            previous_beat, previous_bpm = steps[i - 1]
            seconds += (beat - previous_beat) * 60 / previous_bpm
        b, s, v = float(beat), float(seconds), float(bpm)
        if not all(math.isfinite(x) for x in (b, s, v)) or (i and (
                b <= beat_map[-1]['start_beats'] or s <= seconds_map[-1]['start_seconds'])):
            raise ValueError('Tempo map exceeds interoperable floating-point precision')
        beat_map.append({'start_beats': b, 'bpm': v})
        seconds_map.append({'start_seconds': s, 'bpm': v})
    return {
        'status': 'ok', 'schema': 'film-tempo/v1', 'source_set_path': str(path),
        'source_sha256': hashlib.sha256(raw).hexdigest(), 'source_version': dict(root.attrib),
        'mode': 'constant' if len(steps) == 1 else 'step_map',
        'tempo_bpm': float(steps[0][1]) if len(steps) == 1 else None,
        'tempo_map_beats': beat_map, 'tempo_map_seconds': seconds_map,
        'manual_bpm': float(manual), 'automation_target_id': target,
        'event_count': len(events), 'saved_only': True, 'verified_in_live': False,
        'coverage': {'start_beats': 0, 'start_seconds': 0, 'final_tempo_extends': True},
        'warnings': [
            'Saved ALS snapshot only; unsaved Live changes and runtime tempo control are not verified.',
            'Both maps are relative to arrangement zero. Cue-planner direct use requires '
            'picture origin at arrangement zero; otherwise explicitly rebase the map.',
            'Manual BPM is saved playhead state, not a substitute for the complete tempo envelope.',
        ],
    }
