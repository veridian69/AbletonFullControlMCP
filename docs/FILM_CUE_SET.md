# Offline film locator set copies

`film_cues_export_set` writes a **new saved-set copy**, without connecting to Live.
The native locator importer remains permanently write-disabled. The exporter
never opens a set, changes the running set, saves Live, starts transport or
touches source media. Unsaved changes in Live are not included.

## Call

```json
{
  "name": "film_cues_export_set",
  "arguments": {
    "source_set_path": "D:/Music/Film Project/score.als",
    "output_set_path": "D:/Music/Film Project/score-with-cues.als",
    "cue_sheet": {
      "schema": "film-cues/v1",
      "frame_rate": {"numerator": 24, "denominator": 1},
      "timeline": {"name": "Film", "start_frame": 86400, "start_timecode": "01:00:00:00"},
      "cues": [{"id": "marker:24", "name": "Entrance", "note": "", "color": "Blue",
        "custom_data": "", "start_frame": 24, "duration_frames": 1,
        "start_seconds": 1, "duration_seconds": 0.041666666666666664}]
    },
    "tempo_bpm": 120,
    "arrangement_start_beats": 0,
    "cue_namespace": "jim-ch5",
    "confirm_tempo": true
  }
}
```

Pass the actual Resolve-exported object as `cue_sheet`, not a filename/string.
Timing arguments match `film_cues_plan`: exactly one constant `tempo_bpm` or
explicit step `tempo_map`. `confirm_tempo=true` asserts that the **saved source
set's entire tempo model** matches the supplied assumption. No tempo automation
is read, inferred or changed. Cue frames remain relative to picture start;
`timeline.start_frame` and `start_timecode` are descriptive only.

Returns source/output paths and SHA-256 hashes of both compressed files and
decompressed XML, the full plan, counts of added/preserved locators,
`unrelated_xml_preserved: true`, and `live_set_opened: false`.
Errors return `status: error`. Loading the copy in Live remains a separate
validation step; a valid XML copy is not a claim that Live has loaded it.

## Copy and safety contract

- Source and output must be absolute `.als` paths with the **same existing
  parent directory**. This preserves relative asset reference bases without
  rewriting references. Media availability itself is not checked.
- The source is read only. Existing outputs always refuse, **including exact
  repeats**. There is no overwrite, replacement, relocation or force option.
- Paths containing symlinks, junctions/reparse points, alternate streams or
  reserved filenames refuse. Local hard-link support is required for publication.
- Input gzip is bounded to 16 MiB compressed and 64 MiB expanded. XML must be
  UTF-8, without DTD/entity declarations; nesting is bounded to 256 levels.
- Version/topology validation requires the observed profiles below, one LiveSet,
  Tracks/MainTrack and one nested `Locators/Locators` container. Unknown locator
  fields, duplicate/invalid IDs or malformed values refuse rather than guessing.
- Only the inner locator container is spliced. All unrelated XML bytes,
  existing locator bytes, whitespace, comments, sample paths, tracks, automation,
  ID counters and source gzip bytes are preserved. Output gzip is newly encoded.
- Existing locator positions within the plan's half-frame comparison tolerance,
  or an existing matching ownership tag anywhere, refuse before file creation.
  No existing locator is renamed, moved or removed. New local IDs follow the
  largest existing locator ID; document-global pointee counters are untouched.
- Adjacent frame times can be represented directly in the offline XML; there is
  no native toggle or grid snapping. This does not assert runtime load validation.
- The complete result is re-parsed before writing. A uniquely owned temporary
  is written, flushed and verified, and the source is rechecked before publication.
  The original temporary handle stays open through publication, blocking pathname
  replacement on Windows. An atomic hard link publishes only to an absent output;
  a competing output creator wins safely. The published file's identity and bytes
  must match the verified temporary before any success receipt. No overwrite fallback.
  Cleanup reopens the temporary without following a final reparse point, verifies
  its device/file identity, and uses Windows handle-bound file disposition; it never
  performs a path-based unlink. An unowned replacement is preserved. If ownership
  or safe cleanup cannot be established (including non-Windows platforms), the path
  is left untouched and successful receipts report `temporary_retained`.
  Publication errors never delete the output: inspect any concurrently changed
  output manually. No success hash is returned for a mismatched publication.
  Do not edit/replace source files or their directories concurrently with export.

## Inspected format evidence

- Actual Ch5 saved source, read-only:
  `D:/AI/ch5-pilot/20260826-ch5-pilot/score/ch05_v6 Project/ch05_v7.als`.
  Header: `MajorVersion=5`, `MinorVersion=12.0_12402`,
  `SchemaChangeCount=5`, `Creator=Ableton Live 12.4.5`.
  It has the empty `<Locators><Locators /></Locators>` structure.
- A populated [Live 12.3.6 validation set](https://github.com/evanmusial/ableton-live-tools/blob/main/examples/validation/RYM_2026-03.als)
  was inspected in memory, not added to this repository.
  Compressed SHA-256:
  `132b006aaaa320d07cdbb52ddc42001d2563029c9c1950beb3f04840a33a1989`.
  Header: `5`, `12.0_12300`, `1`, `Ableton Live 12.3.6`.
  Its locator nodes have local `Id` attributes and ordered fields:
  `LomId Value="0"`, `Time`, `Name`, `Annotation`, `IsSongStart`.
  Fractional beat times are present. Names are XML-escaped.
- These are the only accepted header profiles. Using the populated 12.3.6
  locator shape in Ch5's observed 12.4.5 container is an explicit compatibility
  inference, not a completed Live load test. Older factory Live 9/10 examples
  were inspected but are not accepted; Live 9 uses a different ID layout.

The existing `local_automation/als_synth.py` confirms gzip/XML handling but
rebuilds track trees and serialises the whole document. This tool deliberately
does not reuse that mutation path.

## Tests

```powershell
.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_film_cue_set.py tests/test_als_synth.py tests/test_film_cues.py tests/test_film_cues_write_disabled.py
```

Synthetic fixtures model the observed locator structure and keep unrelated
XML/asset references as preservation sentinels. Tests cover exact source/XML
preservation, adjacent times, existing IDs, collisions, no overwrite/repeats,
same-parent restriction, gzip/XML/version bounds, source changes and output
publication races, temporary replacement before/after linking, cleanup ownership
and post-link content corruption. No test opens or modifies the running Live set.
