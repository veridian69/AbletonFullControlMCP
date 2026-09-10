# Film cue interchange — read-only

`film_cues_plan` consumes Resolve `film-cues/v1` exports offline.
`film_cues_import` provides read-only comparison with existing Live locators.
**All film locator writes are permanently disabled, with no override.**

Two disposable live tests demonstrated that native `set_or_delete_cue()`
could delete a preceding locator at beat 2 when asked to create at beat
2.083333. Verified seek and half-frame proximity checks did not prevent it.
A second test returned `False` from `is_cue_point_selected()` and still deleted
the preceding locator. Main verified the original empty locator snapshot and
transport position were restored after both tests. No user locators were involved.

Neither adjacent nor well-separated automatic insertion is claimed safe.
The [official Song API](https://docs.cycling74.com/apiref/lom/song/) documents
the selection query and toggle, but gives no matching snapping/deletion contract.
No guessed radius, playback workaround or further mutation probe is authorised.

## MCP calls

The planner takes the exported JSON object directly:

```json
{
  "name": "film_cues_plan",
  "arguments": {
    "cue_sheet": {
      "schema": "film-cues/v1",
      "frame_rate": {"numerator": 24, "denominator": 1},
      "timeline": {"name": "becomingJim Ch5", "start_frame": 86400, "start_timecode": "01:00:00:00"},
      "cues": [
        {"id": "marker:240", "name": "Entrance", "note": "Sparse tonal bed",
         "color": "Blue", "custom_data": "", "start_frame": 240,
         "duration_frames": 48, "start_seconds": 10, "duration_seconds": 2}
      ]
    },
    "tempo_bpm": 120,
    "arrangement_start_beats": 8,
    "cue_namespace": "jim-ch5"
  }
}
```

Result: `{status: "ok", plan: {...}}`. This cue starts at beat **28** and ends
at beat **32**. The planned locator is `[film:jim-ch5:marker:240] Entrance`.
Invalid input returns `{status: "error", error: "..."}` without any Live access.

To preview, use the same arguments with tool name `film_cues_import`.
`preview` defaults to `true`; `confirm_tempo` remains for compatibility only.
Explicit `preview=false` returns `status: "refused"`,
`writes_enabled: false`, `write_outcome: "not_written"` before planning or
connecting. The bridge independently refuses any `preview` value other than
literal `True`, before reading Live. No seek, toggle or job is attempted.

Preview returns the plan, hypothetical `would_add`, matching `skipped`,
`conflicts`, current-position `tempo_check`, and unchanged transport snapshots.
`added` and `created_unlabelled` remain empty. Every preview reports
`writes_enabled: false` and a permanent write blocker. A clean preview is
**not** permission or proof that insertion would be safe.

## Time and identity contracts

- Frame integers are authoritative and relative to picture start; durations
  must be at least one frame. Seconds are checked against the rational rate
  within 1 microsecond and 1/1000 frame. Up to 500 cues and 1000 tempo segments.
- `timeline.start_frame` and `start_timecode` are descriptive metadata, not
  offsets added to relative cue timing. No SMPTE/drop-frame parsing.
- Supply exactly one of `tempo_bpm` or `tempo_map`. A map is
  `[{"start_seconds": 0, "bpm": 120}, {"start_seconds": 10, "bpm": 60}]`.
  Segments strictly increase from second zero; the last extends indefinitely.
  Start/end beats integrate step changes. Tempo ramps are unsupported.
- `arrangement_start_beats` places picture second zero in Live's quarter-note
  beat units. Neither tool reads or writes tempo automation. The preview checks
  current BPM only within supplied map coverage, within 0.001 BPM. Before the
  picture origin, `tempo_check.status` is `outside_coverage`, `ok` and
  `expected_bpm` are null and `verified` is false; no backwards extrapolation.
  Even a match does not verify the full map. Confirmation cannot enable writes.
- IDs must be unique `marker:<start_frame>` strings. Use a stable namespace
  of 1–64 letters, digits, dots, underscores or hyphens per film/timeline.
  Proposed names carry namespace and ID; labels are limited to 200 characters.
  Notes, colour and custom data remain in the plan. Resolve may filter colour.
- Read-only comparison recognises one matching tag/name within half a frame;
  changed names, moved/duplicate tags and foreign cues in that window conflict.
  This is comparison tolerance, **not** a native toggle safety boundary.
  Nothing is renamed, removed, added or moved.

## Bridge deployment and retired jobs

The read-only implementation is entirely in
`live_remote_script/AbletonFullControlBridge/handlers/project.py`.
Deploy its updated `import_film_cues` and unconditional-refusal
`film_cues_step` compatibility stub, preserving other installed handlers.
The existing `system.reload` reloads the handler without restarting Live.
Do not replace the installed `bridge_server.py`.

The phased writer and MCP `film_cues_recover` tool have been removed.
The step stub refuses both normal polling and cancellation without accessing
Live or advancing a generator. It returns unknown historical write outcome and
unverified restoration; it cannot recover earlier effects. Inspect those manually.
Existing job references are retained inert across hot reload: clearing or closing
old generators could execute their legacy `finally` restoration blocks.

Repository versions remain 1.7.0. An additively reloaded installed server may
still report 1.6.0. Preview checks for `project.import_film_cues`; a missing
handler returns `unavailable`. The MCP wrapper always sends `preview=True`
and never polls even an unexpected pending reply. Offline planning needs no bridge.

## Verification

Run from this repository in Windows PowerShell:

```powershell
.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_film_cues.py tests/test_film_cues_write_disabled.py tests/test_audio_analysis.py tests/test_server_registers.py tests/test_bridge_version_handshake.py
```

Tests exercise the real planner, MCP wrappers and read-only bridge with synthetic
documents whose seek/toggle paths fail immediately. Write-refusal tests prohibit
Live access, connection and old-job advancement. Obsolete writer-success and
recovery tests were removed with that implementation. Audio analysis uses a
temporary synthetic tonal/pulse WAV, never source media or the open set.

Live verification is limited to export -> plan -> preview, plus explicit write
refusal and unchanged locator/transport snapshots. No temporary locator insertion,
cleanup toggle, project save/reload or bounce is part of this verification.

## Safe copies and stem delivery

For automatic locator placement, use the [offline ALS-copy exporter](FILM_CUE_SET.md).
It creates a new sibling set and never invokes the unsafe native toggle or
replaces the open set. Native film writes remain disabled.

Region bounces accept an [explicit step tempo map](BOUNCE_TEMPO_MAP.md), while
the no-map fallback is labelled as a constant-current-tempo assumption. The
real-time recorder still has capture latency and settling time. Use
[film stem packaging](FILM_STEMS.md) for sample-exact common output boundaries
and explicit tails from existing WAVs with verified origins. Packaging does not
prove capture alignment or infer tempo automation. Freeze exports still follow
track clip spans, not a requested cue duration.

`audio_analyze` now handles librosa's scalar or single-element array tempo
under NumPy 2. Its estimated tempo and strongest pitch class remain advisory,
especially for sparse, sustained scoring; they are not a tempo map or a
reliable harmonic analysis.
