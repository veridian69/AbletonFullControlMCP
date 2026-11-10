# Arrangement MIDI import and verified Save As

The checkout is updated and the two installed handler modules are hot-reloaded. New MCP tool exposure still requires a client-host refresh; no commit, push or lock was made. Source bridge version 1.8.0. The handler-only hot reload leaves the running bridge-server metadata at 1.6.0 until its next normal restart.

## MCP tools

- `arrangement_copy_session_clip(track_index, slot_index, position_beats)` copies the Session clip natively, refuses any destination overlap, and verifies exactly one new clip at the requested boundaries. It preserves the source.
- `midi_file_load_into_arrangement(path, track_index, position_beats, destination_bpm, instrument_index=0, tempo_policy="preserve_seconds")` converts the MIDI's elapsed timing through its complete source tempo map to an explicitly supplied constant destination BPM. It checks current Live BPM and never changes global tempo. Destination tempo automation must be absent; note and placement verification do not certify playback under automation. MIDI destination and note bounds are validated on the Live main thread. Live versions with native Arrangement creation use `Track.create_midi_clip(start, length)` (verified on Live 12.4.6), correcting the former start/end bug; older builds stage in one vacant Session slot and clean up only that slot. Existing clips are never cleared. Note count, pitch, onset, duration, velocity and mute are verified; dropped/merged/shortened notes return structured `verification_failed`, even when the count is unchanged. Failure after creation leaves the new clip inspectable; do not retry blindly.
- `op_save_set_as(path)` and `op_save_set(path=...)` honour a new absolute local `.als` path. Overwrites are refused. Windows UI support is narrowly limited to identifiable classic Windows Save As dialogs in the bridge-reported local Ableton Live process. Unknown/custom dialogs return `unsupported`. Saved success requires a newly written, valid gzip/XML Ableton Set and matching active Live Set file path. Missing active-path support returns `unverified`, even if a file was written. Plain `op_save_set()` now uses the verified native Windows Ctrl+S fallback described below.

The older Session MIDI loader now integrates all MIDI tempo segments instead of using the first tempo for every note. Its existing tempo semantics otherwise remain unchanged.

## Verification and deployment

Focused tests use fake Live/Windows adapters, never the real UI. The repository suite was run with the staged source selected by `PYTHONPATH`; 1147 passed, 1 skipped, 3 dependency deprecation warnings in the final full run (135.69 s). All 27 focused tests passed, including overwrite/cancellation safety, cross-process filename readback, note-loss reporting, and nonzero-position start/length semantics. Do not confuse those tests with a live Save As proof.

`extension.patch` and `extension-manifest.json` identify the exact file changes. `apply_extension.py` performs a full source-hash preflight before writing anything, refuses unrelated drift, and is idempotent. Review the patch and apply it to the MCP checkout only after the active music test is safe to disturb. The Remote Script must be installed/hot-reloaded, and the actual running MCP process/source identified and refreshed, before new tools can be claimed deployed.

The opt-in `live_extension_proof.py --save-as <new-absolute-path.als>` exercises Save As directly from the staged code after bridge update. It changes the active lab Set's save location; it must be explicitly coordinated. Unit tests never invoke it.

## Live proof — 5 October 2026

- Live 12.4.6: a two-note MIDI fixture imported and read back exactly in a scratch track.
- Native Session -> Arrangement transfer verified, followed by overlap refusal with unchanged clip list.
- Seed 4102 instrument 0 imported all 300 notes but correctly returned `verification_failed`: pitch/velocity/mute matched, maximum onset difference was 9.37e-8 beats, but the largest duration change was 0.0119998 beats (about 10 ms at 72 BPM). This is not certified as a lossless import.
- Scratch track removed; original 13 tracks, 72 BPM, 459.2 beat arrangement and idle transport restored.
- Save As verified the new `AfterImage-LLMmusic-verified.als` in the existing lab project: valid gzip/XML, 925405 bytes and exact matching active Live path. Overwrite was never approved.

The checkout and installed handler files are updated; the current MCP host has not restarted, so its new tool registration is not yet exposed. That refresh is still required. No commit or push was made.

## Separate observed defect

During the parallel music bakeoff, region bounces at starts 0/96/192 reported different files but captured the same first take (full-rate waveform cross-correlation 1.000 after capture offsets). Clip.move was unavailable. The Director deleted and recreated only lab zero-start MIDI clips from the raw files, bounced from zero, then restored seed 1. This extension does not fix or certify region bouncing. The actual running MCP process/source needs identification before diagnosing installed versus checkout behaviour.

## Current Save fallback — 5 October 2026

Pathless op_save_set now uses a narrow native Windows Ctrl+S helper instead of the unsupported Live document.save method. It verifies the bridge-reported executable/PID and unique main window, refuses any other visible Live window or modal, and verifies a fresh valid gzip/XML ALS rewrite plus unchanged active path. An unsaved or missing ALS returns needs_save_as without keyboard input. A keystroke, clean title or unchanged file alone is never success. Save As still refuses overwrites and its existing implementation is unchanged.

Windows can deny background foreground acquisition. One bounded input-queue focus attempt is detached before sending keys; if focus still fails, Save returns unsupported without Ctrl+S. Bring Ableton to the foreground manually and retry. This helper uses native Windows input internally, without a Codex desktop-control session or overlay.

The Save fallback's 23 focused tests passed (no-write, invalid ALS, wrong active path, unsaved/missing path, dialog refusal, focus detach/key releases and existing Save As regressions). The first live proof returned a structured foreground failure; no key or write was claimed. After the Director brought Ableton forward, native Ctrl+S was sent through a fresh MCP process, but Live did not rewrite the already clean Set: mtime remained 1791195276840571000 and size remained 925569 bytes. The operation returned unverified with write_observed=false. This demonstrates safe request dispatch and refusal to invent disk-save evidence; it is not a successful rewrite proof. No musical data was changed merely to dirty the Set. See score-tests/mcp-save-current/live-save-proof.json for the exact latest proof.

Only MCP server Python source changed; no bridge handler update or Live restart is needed. A fresh MCP process can use it immediately. Existing long-running MCP instances need reconnect/refresh to load the new pathless Save implementation. No commit or push was made.


