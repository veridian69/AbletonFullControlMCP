# Local film-control extensions

This branch extends the existing Python MCP. Go is not required. It remains a
local development branch; the connector currently loaded by Codex still runs
the original checkout. The pilot and AFTERIMAGE files have not been changed.

## Master capture

`render_master` now uses the existing native resampling engine. Example MCP
arguments for an 80-second Arrangement at a constant 96 BPM:

```json
{"output_path":"D:/AI/test-render/master.wav","length_beats":128,"background":true}
```

Poll the returned `job_id` with `bounce_job_status`. The final result contains
`status`, the `wav` capture result, and timing metadata. `status: ok` requires
the engine to verify routing/arming and report a copied recording. Foreground
and background bounce tools share a transport reservation and return `busy`
when another capture is running. Cancellation propagates to the engine
for cleanup. Capture begins at Arrangement beat zero and takes real time.
Omitting `length_beats` reads Live's `song_length`; this may include material
well beyond the intended film ending. Existing output WAVs may be overwritten.

Without a tempo map, timing assumes the current tempo remains constant. For
step tempo changes, pass `tempo_map`, e.g.:

```json
{"output_path":"D:/AI/test-render/master.wav","length_beats":16,
 "tempo_map":[{"start_beats":0,"bpm":120},{"start_beats":8,"bpm":96}]}
```

Neither path discovers or verifies tempo automation. Use `bounce_region` for a
nonzero start. `render_clip` remains an explicitly unsupported legacy tool;
this change does not implement Session-clip rendering.

## Other controls

- [Stem capture](STEM_CAPTURE.md): advertised source routes, Post Mixer taps,
  collective arm verification and setup cleanup.
- `arrangement_set_notes` and `arrangement_get_notes`: explicit Arrangement
  clip addressing, replacement and readback verification. Generic `clip_*`
  OSC controls use Session slot indices.
- [Automation](ARRANGEMENT_AUTOMATION.md): `automation_capabilities`,
  `automation_write_curve`, `automation_read_values`:
  runtime-checked persistent clip envelopes for mixer/device parameters.
  These are clip envelopes, not global Arrangement track automation lanes.
- [Windows file operations](windows-file-operations.md): explicit GUI tools,
  verified Save As files, and honest pending stages for New/native Export.

See [the generated tool reference](TOOLS.md) for complete argument schemas.

## Loading this branch later

The MCP server source and installed Live bridge must both be updated. Bridge
1.8.0 adds the automation handler namespace; a handler hot reload alone cannot
load changes to `bridge_server.py`. Reload the control surface or restart Live
after installing the bridge, with the working set safely saved first.

To inspect the bridge install destination without changing it:

```powershell
Set-Location D:/AI/Dad-MCPs/AbletonFullControlMCP-master-render
$env:PYTHONPATH = "$PWD/src"
& D:/AI/Dad-MCPs/AbletonFullControlMCP/.venv/Scripts/python.exe -m ableton_mcp.scripts.install_bridge --dry-run
```

The loaded connector's modified `.mcp.json` has deliberately not been copied or
rewritten. No install/reload/push is performed by the test commands.

## Validation boundary

Validation on 12 September 2026:

- Full repository suite: 1,175 passed, 1 skipped, 3 warnings in 133.67 seconds.
  [Captured output](validation/2026-09-12-full-suite.txt).
- Combined affected-feature suite: 284 passed.
- After adding the final early-cancellation regression and completion callback:
  fresh jobs/render/progress suite, 49 passed. The full-suite run above began
  before this last regression was added; it is not a claim of 1,177 tests run.
- Independent review: master routing, capture exclusivity and early cancellation
  findings resolved. Whitespace checks passed.

Reproduce the final suite from the worktree with the interpreter/PYTHONPATH
shown above and `-m pytest -q`.

Tests use fake Live, OSC and Windows backends. They verify routing decisions,
argument validation, readback failures, cleanup and tool registration. They do
not establish audio quality or prove that a particular Live build exposes the
optional envelope methods. Native file-dialog layouts likewise require a live
smoke test. Use a disposable set for that final integration check.
