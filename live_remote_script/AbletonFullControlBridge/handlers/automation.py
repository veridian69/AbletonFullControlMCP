"""Runtime-probed clip automation. No parameter.value writes or UI emulation."""
from __future__ import absolute_import
import math

EXPORTS = ('capabilities', 'write_steps', 'read_values')


def _index(items, index, label):
    i = int(index)
    if i < 0 or i >= len(items):
        raise ValueError('%s index out of range' % label)
    return items[i]


def _resolve(track_index=0, clip_index=0, view='arrangement', target='pan',
             device_index=0, parameter_index=0, send_index=0, **_):
    from .clips import _track
    if int(track_index) < 0:
        raise ValueError('track_index must be non-negative')
    track = _track(int(track_index))
    if view == 'arrangement':
        clip = _index(list(track.arrangement_clips), clip_index, 'clip')
    elif view == 'session':
        clip = _index(list(track.clip_slots), clip_index, 'slot').clip
        if clip is None:
            raise ValueError('Session slot is empty')
    else:
        raise ValueError('view must be arrangement or session')
    if target == 'pan':
        parameter = track.mixer_device.panning
    elif target == 'volume':
        parameter = track.mixer_device.volume
    elif target == 'send':
        parameter = _index(list(track.mixer_device.sends), send_index, 'send')
    elif target == 'device':
        device = _index(list(track.devices), device_index, 'device')
        parameter = _index(list(device.parameters), parameter_index, 'parameter')
    else:
        raise ValueError('target must be pan, volume, send or device')
    return clip, parameter


def _probe(clip, parameter):
    getter = getattr(clip, 'automation_envelope', None)
    creator = getattr(clip, 'create_automation_envelope', None)
    result = {'status': 'unsupported', 'scope': 'clip_envelope',
              'time_domain': 'clip-local beats', 'interpolation': 'steps',
              'parameter_min': float(parameter.min), 'parameter_max': float(parameter.max),
              'can_get': callable(getter), 'can_create': callable(creator),
              'can_write': False, 'can_read': False, 'envelope_exists': False}
    envelope = None
    if callable(getter):
        try:
            envelope = getter(parameter)
        except Exception as exc:
            result['reason'] = 'automation_envelope failed: %s' % exc
            return result, None
    if envelope is not None:
        result.update(envelope_exists=True,
                      can_write=callable(getattr(envelope, 'insert_step', None)),
                      can_read=callable(getattr(envelope, 'value_at_time', None)))
    if result['can_write'] and result['can_read']:
        result['status'] = 'ok'
    elif callable(getter) and callable(creator) and envelope is None:
        result['status'] = 'creation_required'
        result['reason'] = 'Envelope methods can only be checked after creation'
    else:
        result['reason'] = 'This Live Python object does not expose verified envelope read/write methods'
    return result, envelope


def capabilities(c_instance, **kwargs):
    """Read-only probe. Does not create an envelope or move a parameter."""
    clip, parameter = _resolve(**kwargs)
    return _probe(clip, parameter)[0]


def _finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('values and times must be finite')
    return result


def write_steps(c_instance, steps=None, **kwargs):
    """Insert non-overlapping clip-local steps; read their midpoint values back.

    Updates the supplied intervals only. This is not a global track lane and
    does not clear existing envelopes. Creation/writes can partially mutate
    before a Live exception; returned errors state that explicitly.
    """
    clip, parameter = _resolve(**kwargs)
    rows = []
    if not steps or len(steps) > 4096:
        raise ValueError('provide 1..4096 steps')
    previous_end = -1.0
    for step in steps:
        start, duration, value = [_finite(step[k]) for k in ('start', 'duration', 'value')]
        if start < 0 or duration <= 0 or start < previous_end - 1e-9:
            raise ValueError('steps must have positive duration, sorted non-overlapping non-negative times')
        if not float(parameter.min) <= value <= float(parameter.max):
            raise ValueError('value is outside the parameter native range')
        if not math.isfinite(start + duration):
            raise ValueError('step end must be finite')
        if getattr(parameter, 'is_quantized', False) and value != round(value):
            raise ValueError('quantized parameters require integer values')
        previous_end = start + duration
        rows.append((start, duration, value))
    if not getattr(parameter, 'is_enabled', True):
        raise ValueError('parameter is disabled')
    probe, envelope = _probe(clip, parameter)
    if probe['status'] == 'unsupported':
        return probe
    written = 0
    created = False
    try:
        if envelope is None:
            envelope = clip.create_automation_envelope(parameter)
            created = True
            if envelope is None:
                envelope = clip.automation_envelope(parameter)
        if not callable(getattr(envelope, 'insert_step', None)) or not callable(getattr(envelope, 'value_at_time', None)):
            return dict(probe, status='unsupported', reason='Created envelope lacks insert_step/value_at_time',
                        mutation_possible=created)
        for start, duration, value in rows:
            envelope.insert_step(start, duration, value)
            written += 1
        observed = [float(envelope.value_at_time(s + d / 2.0)) for s, d, v in rows]
        verified = all(abs(actual - row[2]) <= 1e-5 * max(1.0, abs(row[2]))
                       for actual, row in zip(observed, rows))
        return {'status': 'ok' if verified else 'error', 'scope': 'clip_envelope',
                'interpolation': 'steps', 'steps_written': written,
                'verified': verified, 'verification': 'interval midpoint samples',
                'values': observed, 'created': created,
                'mutation_possible': True}
    except Exception as exc:
        return {'status': 'error', 'error': str(exc), 'steps_written': written,
                'verified': False, 'mutation_possible': True}


def read_values(c_instance, times=None, **kwargs):
    clip, parameter = _resolve(**kwargs)
    if not times or len(times) > 4096:
        raise ValueError('provide 1..4096 sample times')
    times = [_finite(t) for t in times]
    if any(t < 0 for t in times):
        raise ValueError('sample times must be non-negative')
    probe, envelope = _probe(clip, parameter)
    if not probe['can_read']:
        return dict(probe, status='unsupported', reason='No readable envelope exists')
    return {'status': 'ok', 'scope': 'clip_envelope', 'times': times,
            'values': [float(envelope.value_at_time(t)) for t in times]}
