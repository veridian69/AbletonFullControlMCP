# Arrangement notes and persistent clip automation

`arrangement_get_notes(track_index, clip_index)` and
`arrangement_set_notes(track_index, clip_index, notes)` address
`Track.arrangement_clips`. The generic `clip_get_notes` / `clip_add_notes`
OSC tools address Session slots; an Arrangement index is not interchangeable.

Replacement validates every note before removal, stops on removal failure,
and reads the notes back before reporting success. Notes have `pitch`,
`start`, `duration`, `velocity`, and optional `mute`; times are clip-local
beats. Empty notes clears the clip. Read/removal spans the supported positive
Live timeline (0 through 1,576,800 beats), including content beyond the visible
clip edge. Extended expression/probability fields are not preserved. A failed
write or readback can leave changes; there is no automatic rollback or save.

## Automation

- `automation_capabilities`: read-only probe of the actual clip and parameter.
- `automation_write_curve`: write persistent envelope steps from `{time, value}`
  points; verify interval midpoint samples.
- `automation_read_values`: sample envelope values at clip-local beat times.

All use zero-based `track_index`, `clip_index` and `view` (`arrangement` or
`session`; Session uses the slot index). Select `target="pan"`, `target="volume"`,
`target="send"` with `send_index`, or `target="device"` with `device_index`
and `parameter_index`. Device targeting addresses top-level devices; choose
an existing filter cutoff parameter by its enumerated index. Native parameter
ranges are returned by the capability probe. Quantized parameters require
integer step values.

Example arguments for a pan sweep across four beats:

```json
{
  "track_index": 0,
  "clip_index": 0,
  "view": "arrangement",
  "target": "pan",
  "points": [{"time": 0, "value": -1}, {"time": 4, "value": 1}],
  "resolution_beats": 0.0625
}
```

Curves approximate piecewise-linear interpolation using right-edge sampled
steps, at the requested resolution or finer (maximum 4096 steps). Each
interval takes its right-edge value: the final target is reached in the final
step, up to one step early. This is a staircase, not native continuous ramp
interpolation. Existing envelope content outside the inserted intervals is
not cleared. Midpoint readback verifies samples, not the complete shape or
an audible playback result.

This is **clip-envelope automation**, not global Arrangement track-lane
editing. Times are clip-local and follow the clip's loop/playback behaviour.
The tool never simulates automation by assigning `parameter.value`.
Changes become part of the saved set when the user saves; the tools do not
save automatically.

## Runtime support and validation limits

Bridge 1.8.0 exposes these automation handlers. The Python envelope API varies
with Live versions and is not fully documented in the public
[Clip LOM reference](https://docs.cycling74.com/apiref/lom/clip/).
The public reference documents clearing envelopes; it does not establish that
Python `automation_envelope`, `create_automation_envelope`, `insert_step`, and
`value_at_time` are present in a particular installed Live version.

The capability probe checks methods on the actual object without creating an
envelope. If no envelope exists but creation is available, it returns
`creation_required`; write/read support can only be checked after creation.
A write that encounters absent methods returns `unsupported`, with
`mutation_possible` when an empty envelope may already have been created.
An exception or readback mismatch returns `error`, never successful automation.
Writes can partially complete before an exception, and are not rolled back.

Unit tests use fake Live objects for routing, capability detection, numeric
validation and readback failure. No running Live set was modified for these
tests. Actual API availability, envelope timing/loop behaviour, audible
results and save/reopen persistence still require a disposable Live 11.3.43
set. The public reference currently describes newer Live versions, so it is
not evidence of Live 11 support.


For a volume fade to silence, first read the envelope value at the fade start
with `automation_read_values`, or explicitly choose its initial value in the
native 0..1 volume range. For example, a chosen 0.8-to-zero fade:

```json
{
  "track_index": 0,
  "clip_index": 0,
  "target": "volume",
  "points": [{"time": 12, "value": 0.8}, {"time": 16, "value": 0}],
  "resolution_beats": 0.0625
}
```

The final step carries exactly `0`, so it reaches silence by beat 16. Native
volume is not a dB value; a linear envelope in these units is not a specified
linear-dB fade. Unsupported envelope APIs still return `unsupported`.
