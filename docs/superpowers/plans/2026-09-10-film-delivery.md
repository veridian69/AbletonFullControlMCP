# Film Delivery Implementation Plan

**Goal:** Finish the safe film handoff with exact stem packaging and copy-based locator insertion, then publish verified changes.

**Architecture:** Treat existing WAV captures as immutable inputs with explicit sample origins. Package exact common-start outputs in a new directory and independently audit them. Insert locators into a new sibling ALS copy, never through Live's unsafe native toggle. Preserve all pre-existing source changes.

**Tech stack:** Python, soundfile, NumPy, rational arithmetic, bounded gzip/XML, MCP.

## Constraints

- Do not infer capture alignment from waveform silence or current BPM.
- No source/output overwrite, live locator toggles, automatic project replacement, or source media changes.
- Native film locator writes remain disabled. Saved-set copies must preserve unrelated XML and asset resolution.
- Git metadata uses Val <val@jclk.ch>; exclude the user's pre-existing .mcp.json change.
- User explicitly requested implementation without routine approval pauses and authorised commits/pushes.

## Tasks

- [x] Stem delivery: added exact WAV packaging, manifest audit, docs and test-first coverage. Twenty focused tests pass; real MCP synthetic delivery passes. Captured origins remain explicit caller assertions.
- [x] Safe locator copies: added bounded ALS sibling-copy exporter and MCP wrapper. Native write gates remain intact. Thirty-two tests cover preservation, collisions, format bounds and concurrent publication/cleanup.
- [x] Integrate registrations and generated tool documentation; focused suite and full suite pass. Real MCP synthetic files and read-only application probes pass. File verification is not a Live load test.
- [x] Inspect client registrations without disclosing secrets. Added Codex registrations and verified fresh connections from those exact entries. Current host reload is unavailable; no host/app termination or unsaved-state replacement.
- [x] Review staged diffs; exclude the user's `.mcp.json` edit and local media. Actor-scoped publication to the verified forks is authorised; final commit/push receipts are recorded in the external handoff rather than this commit's own contents.

## Verified artefact

Created `ch05_v7-film-cues-20260910.als` alongside the saved Ch5 score: 35 locators
from Resolve, explicit 120 BPM verified against that saved source, source bytes
unchanged, unrelated XML independently checked. Not opened in Live; unsaved
changes and capture-latency calibration are outside this file-only verification.

Source SHA-256: `c5988f12bc1548a9d1ad7172e243a208cc846263bddb4c1125eb7d999b719d5b`.
Output SHA-256: `4a7ca0d56b43874d5493541ac1e4531bd347a6d30c733f26f3b100612f39b416`.

Publication receipts are maintained in `D:/AI/Dad-MCPs/FILM-MCP-HANDOFF.md`.
