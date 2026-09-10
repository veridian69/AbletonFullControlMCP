# Region bounce timing with explicit tempo steps

`bounce_region` and `bounce_region_all_active` accept an optional `tempo_map`.
The same option is available on `bounce_region_via_resampling` and
`bounce_region_all_active_via_resampling` in `ableton_mcp.bounce.resampling`.

```json
{
  "output_dir": "D:/AI/exports/ch5-region",
  "start_beats": 2,
  "end_beats": 10,
  "tempo_map": [
    {"start_beats": 0, "bpm": 120},
    {"start_beats": 4, "bpm": 60},
    {"start_beats": 8, "bpm": 240}
  ],
  "encode_mp3": false
}
```

Pass these arguments to either region tool. `bounce_region` records the master
by default; add `"track_indices": [0, 2]` to record selected stems. The all-active
tool selects unmuted audio-producing tracks. `background: true` uses the existing
bounce job interface and progress reporting.

## Timing contract

- Map positions are absolute **arrangement beats**, not beats relative to the
  region or picture. Region intervals are `[start_beats, end_beats)`.
- Supply a non-empty list. Each entry has exactly `start_beats` and `bpm`.
  The first entry starts at beat **0**, subsequent starts increase strictly,
  and each BPM is finite and between **20 and 999**, inclusive.
- Steps take effect at their start beat. The final step extends indefinitely,
  so the map covers the whole nonnegative arrangement. Linear ramps are not
  supported. Booleans, numeric strings, NaN and infinity are rejected in maps.
- The duration is the sum of `covered_beats * 60 / bpm` across every intersected
  segment. Calculations use rational decimal arithmetic and convert to floating
  seconds for the existing real-time recorder. The example records a nominal
  **5.5 seconds**: 2 beats at 120, 4 at 60, and 2 at 240 BPM.
- Supplying the map is the explicit request to use that timing assumption.
  There is no additional confirmation flag. The caller must check that Live's
  actual arrangement tempo follows it. The tools do not read, infer, verify,
  create or change tempo automation. An explicit map does not read current BPM.

With a supplied map, the result includes:

```json
{
  "time_basis": "explicit_step_tempo_map",
  "region_start_beats": 2.0,
  "region_end_beats": 10.0,
  "region_seconds": 5.5,
  "tempo_at_bounce": null,
  "tempo_automation_verified": false,
  "tempo_map": [
    {"start_beats": 0, "bpm": 120},
    {"start_beats": 4, "bpm": 60},
    {"start_beats": 8, "bpm": 240}
  ]
}
```

Omitting the map preserves the existing calculation: read current BPM once and
assume it is constant throughout the region. That result explicitly reports
`time_basis: "assumed_current_tempo"`, the sampled `tempo_at_bounce`,
`tempo_map: null`, and `tempo_automation_verified: false`. It is unsuitable for
an arrangement with tempo changes unless that approximation is intentional.

## Validation and capture limits

The entire map, including entries beyond the region, and region options are
validated before directory creation, cleanup, warmup or recording. All-active
bounces also validate and calculate duration before deleting orphan temp tracks.
Static map, range and option errors are rejected before starting a background job.
Region tool MP3 bitrates must be integers between 8 and 320 kbps.

This remains the existing real-time recording engine: warmup, settle time,
capture latency, file finalisation and cleanup keep their existing behaviour.
`region_seconds` is the intended region duration; settle time can add audio after
it. This feature does not promise sample-exact file length, trim latency or align
stems. Sample alignment and delivery packaging remain separate offline work.
Progress callbacks now reach recording for selected and all-active region stems.

## Offline verification

```powershell
.venv/Scripts/python.exe -B -m pytest tests/test_bounce_tempo_map.py -q -p no:cacheprovider
```

Tests exercise real region and MCP argument paths with the external Live/capture
boundary replaced. They do not perform live bounces.
