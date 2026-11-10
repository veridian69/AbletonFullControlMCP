# Saved-set tempo extraction

`read_saved_tempo(source_set_path: str) -> dict` and MCP tool
`film_tempo_read(source_set_path)` inspect a saved ALS without connecting to Live,
writing files or inferring tempo from audio/current playback. Server registration
is owned by main; the module exports `register(mcp)` independently.

## Call and hash-bound evidence

```json
{
  "name": "film_tempo_read",
  "arguments": {
    "source_set_path": "D:/AI/ch5-pilot/20260826-ch5-pilot/score/ch05_v6 Project/ch05_v7.als"
  }
}
```

Successful output has this contract (additional diagnostic fields below):

```json
{
  "status": "ok",
  "schema": "film-tempo/v1",
  "source_set_path": "absolute resolved source path",
  "source_sha256": "SHA-256 of the exact compressed ALS bytes inspected",
  "source_version": {
    "MajorVersion": "5",
    "MinorVersion": "12.0_12402",
    "SchemaChangeCount": "5",
    "Creator": "Ableton Live 12.4.5"
  },
  "mode": "constant",
  "tempo_bpm": 120.0,
  "tempo_map_beats": [{"start_beats": 0.0, "bpm": 120.0}],
  "tempo_map_seconds": [{"start_seconds": 0.0, "bpm": 120.0}],
  "saved_only": true,
  "verified_in_live": false,
  "coverage": {"start_beats": 0, "start_seconds": 0, "final_tempo_extends": true}
}
```

`source_version` preserves all root attributes, including `Revision` when present.
Other fields: `manual_bpm` (saved manual/playhead value), `automation_target_id`,
`event_count`, and `warnings`. Multiple effective segments use `mode: "step_map"`
and `tempo_bpm: null`. Redundant horizontal breakpoints are omitted from the maps.

Preflight must recompute the current source file's SHA-256 and match
`source_sha256`. Path equality alone is insufficient. This is evidence about
that saved snapshot, not unsaved Live state, active configuration, scene launches,
external sync or runtime tempo control. There is no config signature in this tool.

The function raises `ValueError`/`OSError` on unsupported input. The MCP wrapper
returns `status: "error"`, `error`, `saved_only: true`, `verified_in_live: false`,
**without maps**. Never substitute the manual BPM or a partial map after refusal.

## Consumers and origins

- Region bounce: pass `tempo_map_beats` as `tempo_map`. Each entry has exactly
  `start_beats` and `bpm`, matching `bounce/resampling.py`.
- Film cue planner: pass `tempo_map_seconds` as `tempo_map`, with
  `arrangement_start_beats=0`. Its entries use `start_seconds` and `bpm`.
- Both maps start at **arrangement zero**. If picture starts elsewhere, explicitly
  rebase the map first. Merely passing a nonzero `arrangement_start_beats` with
  this unchanged seconds map would shift the tempo changes incorrectly.

For example, beats `[0, 4, 8]` at BPM `[120, 60, 240]` become seconds `[0, 2, 6]`.
Integration uses exact rational arithmetic before checking the JSON float results
remain finite and strictly increasing. BPM is restricted to 20–999 for bounce
compatibility; up to 1000 effective segments for cue-planner compatibility.

## Accepted semantics and refusal rules

The reader resolves the unique
`LiveSet/MainTrack/DeviceChain/Mixer/Tempo/AutomationTarget` to exactly one
arrangement `AutomationEnvelope/EnvelopeTarget/PointeeId`. It does not assume
target ID 8: IDs are resolved and checked for duplicate definitions/references.
Every `AutomationTarget`/`ModulationTarget` ID and `PointeeId` reference is
validated as canonical nonnegative decimal **before** filtering for tempo or
deduplicating. Aliases such as `08`, `+8` and `09` refuse even outside the main
tempo envelope; they cannot hide additional automation/modulation sources.
The complete `Automation/Events` sequence is checked, not a playhead slice.

- Initial state must be a `FloatEvent` at known sentinel `-63072000` or beat zero.
  The sentinel supplies the initial tempo; it is not a negative output segment.
- A positive-duration interval is accepted only when its endpoint BPM values
  are equal. A changed BPM over positive time is a **ramp**, even if no curve
  attributes exist. Small time gaps are not rounded into steps.
- A zero-duration pair of successive breakpoints at the exact same beat can
  make an instantaneous step. The first preserves the preceding horizontal
  interval; the second supplies the new tempo. More than two points at one time,
  repeated sentinels, unordered points or other negative times refuse.
- Curves, extra event attributes, pending transforms, unknown interpolation,
  missing/duplicate targets or envelopes, enabled scene tempo, populated user
  tempo automation and additional tempo/modulation references refuse.
- Any nonempty `SignalModulations` container refuses conservatively, even if
  its exact effect cannot be attributed to tempo. New/unknown tempo-related
  paths also refuse rather than being silently ignored.
- Manual BPM need not equal the first automation point: it can reflect another
  saved playhead position. It is reported but never substitutes for the envelope.

Reads reuse the existing ALS safety helpers without modifying them: at most
16 MiB compressed, 64 MiB expanded UTF-8 XML, nesting depth 256, no DTD/entity
declarations, and only the previously inspected Live 12.3.6 / 12.4.5 profiles.
Input must be an absolute regular `.als` file without symlink/reparse components.
At most 10000 raw tempo events are accepted. Unsupported files remain untouched.

## Inspected evidence

Read-only inspection of the actual saved Ch5 source above established:

- SHA-256: `c5988f12bc1548a9d1ad7172e243a208cc846263bddb4c1125eb7d999b719d5b`.
- Live 12.4.5 / `12.0_12402`, schema change count 5.
- Manual tempo 120; target 8 has one event at `-63072000`, value 120.
- No reference to modulation target 9, empty user-tempo automation, empty signal
  modulation containers; all eight scene tempo switches are disabled.

Serialized breakpoint evidence was also inspected in the authors' public
[dawtool fixtures](https://github.com/offlinemark/dawtool/tree/master/tests/als):
`L12-automation.als` contains changing BPM over positive intervals (ramps), while
`automation-pathological.als` contains horizontal intervals joined by exact
same-time pairs. Their compressed SHA-256 hashes are respectively
`5756a430081aad79c4fac6d4c1d3b0b22f4cf105a3d68971cf17e00a4f1ff11a` and
`56180f912625a9ebdb16544c9e60e6758fa027a3d6262932e9f1efcca174611f`.
The latter is a Live 10 fixture used to inspect breakpoint encoding, **not** a
new supported source-version profile. No public media/sets were copied into the
repository. Ableton's [automation manual](https://www.ableton.com/en/live-manual/11/automation-and-editing-envelopes/)
distinguishes breakpoint segments, drawn steps and curves. No missing curve
attribute is treated as proof of step interpolation.

## Verification

```powershell
.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_film_tempo.py tests/test_film_cues.py tests/test_film_cue_set.py tests/test_bounce_tempo_map.py
```

Fixtures reproduce the inspected topology without media. Tests exercise both
real map consumers' shapes, constant/step/sentinel timing, refusal conditions,
immutable source hashes, gzip/XML limits and standalone MCP calls. Main owns
shared registration and combined preflight integration.
