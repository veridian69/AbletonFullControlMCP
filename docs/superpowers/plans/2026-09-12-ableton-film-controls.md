# Ableton film controls implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the independent tasks and review their combined result.

**Goal:** Replace the film workflow's avoidable GUI workarounds and misleading MCP results with tested controls.

**Architecture:** Keep the existing Python MCP and Live Python bridge. Reuse native resampling, explicitly address Arrangement clips, probe envelope capabilities, and isolate Windows GUI file commands behind clearly named tools.

**Tech stack:** Python 3.11 MCP, Live 11-compatible Python bridge, stdlib Win32, pytest with fake Live/UI backends.

**Spec:** Jon's authorised scope below.

## Scope and constraints

- Local fork only, in `D:/AI/Dad-MCPs/AbletonFullControlMCP-master-render`, branch `val/film-controls-20260912`.
- Preserve the loaded checkout and its modified `.mcp.json`; no upstream push, merge, or automatic connector restart.
- Implement master rendering, reliable multi-stem recording, Arrangement-note writes with readback, persistent musical automation where the bridge supports it, and labelled native Windows file helpers.
- Report unsupported capabilities and pending GUI stages explicitly; never equate sending a command with verified success.
- Do not modify the pilot, experimental film, or running Live set as a development test.

## Tasks

- [x] Baseline: `tests/test_bounce_region.py tests/test_bounce_resampling.py`: 33 passed.
- [x] Master rendering: replace `tools/render.py` stub using `bounce.resampling`, with omitted length read from `/live/song/get/song_length`, tempo metadata, background jobs and capture failure propagation. Regression: `tests/test_render_master.py`, 10 initial failures then 10 passes.
- [x] Stem recording: `bounce/resampling.py`; resolve advertised source routes, verify collective arming before recording, clean temporary tracks on setup failure. Extend fake OSC regression tests.
- [x] Arrangement/automation: `tools/arrangement.py`, `tools/automation.py`, bridge `handlers/clips.py` and `handlers/automation.py`; explicit note write/readback, capability-checked envelope step/curve operations for pan/device/sends. Add fake bridge tests.
- [x] Windows helpers: separate Win32 backend and MCP tools; verify Ableton PID/foreground, Save As via native dialog with file evidence, explicit pending-stage reporting for New/Export. Test with fake UI, do not drive live windows.
- [x] Integrate server and bridge registrations, document tool calls and limitations, run targeted suite and collection/integration checks, review combined changes.

## Reproducible validation

From this worktree in PowerShell:

```powershell
$env:PYTHONPATH = "$PWD/src"
& D:/AI/Dad-MCPs/AbletonFullControlMCP/.venv/Scripts/python.exe -m pytest tests/test_render_master.py tests/test_bounce_region.py tests/test_bounce_resampling.py -q
```

The environment comes from the original checkout, but PYTHONPATH selects the isolated source. New module-specific tests are recorded in the final feature guide. Unit verification does not establish compatibility with a running Live version; deployment and disposable-set audio/UI verification remain separate.
