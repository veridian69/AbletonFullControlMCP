"""Project-level ops: save (and a few introspection helpers)."""

from __future__ import absolute_import

import os


# Retain old generators across hot reload: clearing/closing them could execute
# their finally blocks. Never inspect, advance or close these retired jobs.
_FILM_CUE_JOBS = globals().get("_FILM_CUE_JOBS", {})


EXPORTS = (
    "save",
    "info",
    "get_file_path",
    "list_freezing_dir",
    "import_film_cues",
    "film_cues_step",
)


def save(c_instance, **_):
    """Save the current set. Equivalent to Cmd-S.

    `Application.get_document().save()` invokes Live's normal save path. If the
    set has never been saved Live will pop a Save As dialog on the main thread,
    which is the same behaviour as pressing Cmd-S in the UI.

    Save-As to a specific path is *not* exposed in Live's Python API — it
    requires a UI dialog. We document this by returning `save_as_supported:
    False` if the caller passes `path`.
    """
    import Live  # type: ignore
    app = Live.Application.get_application()
    doc = app.get_document()
    save_fn = getattr(doc, "save", None)
    if save_fn is None:
        raise RuntimeError("Application.get_document().save not available")
    save_fn()
    return {"saved": True, "save_as_supported": False}


def info(c_instance, **_):
    """Return basic project metadata (length, tempo, name)."""
    import Live  # type: ignore
    app = Live.Application.get_application()
    doc = app.get_document()
    return {
        "live_version": "%d.%d.%d" % (
            app.get_major_version(),
            app.get_minor_version(),
            app.get_bugfix_version(),
        ),
        "tempo": float(getattr(doc, "tempo", 0.0)),
        "song_length": float(getattr(doc, "song_length", 0.0)),
        "is_playing": bool(getattr(doc, "is_playing", False)),
        "num_tracks": len(list(getattr(doc, "tracks", []))),
        "num_scenes": len(list(getattr(doc, "scenes", []))),
    }


def _document_file_path():
    """Return the absolute path of the .als file on disk, or None if unsaved.

    Live's LOM doesn't expose this through a single canonical property — we
    try the call/attribute variants that have shipped across Live 10/11/12.
    """
    import Live  # type: ignore
    doc = Live.Application.get_application().get_document()
    # Method-form first (newer Live):
    fn = getattr(doc, "get_file_path", None)
    if callable(fn):
        try:
            p = fn()
            return p if p else None
        except Exception:
            pass
    # Property-form fallback (older Live):
    p = getattr(doc, "file_path", None)
    if p:
        return p
    return None


def get_file_path(c_instance, **_):
    """Return the .als file path for the open project, or None if unsaved."""
    return {"file_path": _document_file_path()}


def list_freezing_dir(c_instance, **_):
    """List wav files in the project's `Samples/Freezing/` directory.

    Returns ``{"freezing_dir": <abs path or None>, "files": [...]}`` where
    each file is ``{"path": str, "mtime": float, "size": int}``. Used by
    the freeze-mode bouncer to detect which file Live wrote during a
    just-completed Track.freeze() call.

    If the project hasn't been saved yet, ``freezing_dir`` is None and
    ``files`` is empty — freeze itself fails in that state, so the bouncer
    surfaces it as a precondition error.
    """
    doc_path = _document_file_path()
    if not doc_path:
        return {"freezing_dir": None, "files": []}
    project_dir = os.path.dirname(doc_path)
    freezing_dir = os.path.join(project_dir, "Samples", "Freezing")
    if not os.path.isdir(freezing_dir):
        return {"freezing_dir": freezing_dir, "files": []}
    files = []
    for name in sorted(os.listdir(freezing_dir)):
        full = os.path.join(freezing_dir, name)
        if not os.path.isfile(full):
            continue
        try:
            stat = os.stat(full)
            files.append({
                "path": full,
                "mtime": float(stat.st_mtime),
                "size": int(stat.st_size),
            })
        except OSError:
            continue
    return {"freezing_dir": freezing_dir, "files": files}


def import_film_cues(c_instance, cues=None, tempo_assumption=None,
                     preview=True, confirm_tempo=False, job_id=None, **_):
    """Read-only locator preview. Writes are permanently disabled; no override."""
    if preview is not True:
        return {"status": "refused", "writes_enabled": False, "write_outcome": "not_written",
                "error": "Film locator writes disabled: native toggle can delete existing locators.",
                "warning": "Use film_cues_plan or preview=True; no seek, toggle or job was attempted."}
    import math
    import Live  # type: ignore

    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    if type(preview) is not bool or type(confirm_tempo) is not bool:
        raise ValueError("preview and confirm_tempo must be booleans")
    if not isinstance(cues, list) or len(cues) > 500:
        raise ValueError("cues must contain at most 500 planned markers")
    tags = set()
    for cue in cues:
        if not isinstance(cue, dict):
            raise ValueError("each planned cue must be an object")
        tag, name = cue.get("locator_tag"), cue.get("locator_name")
        beat, tolerance = cue.get("start_beats"), cue.get("tolerance_beats")
        if (not isinstance(tag, str) or not tag.startswith("[film:") or not tag.endswith("]")
                or not isinstance(cue.get("id"), str) or not cue["id"]
                or tag in tags or not isinstance(name, str) or not name.startswith(tag + " ")
                or not finite(beat) or beat < 0 or not finite(tolerance) or tolerance <= 0):
            raise ValueError("invalid or duplicate planned locator")
        tags.add(tag)
    segments = (tempo_assumption or {}).get("segments", [])
    if not isinstance(segments, list) or not segments:
        raise ValueError("explicit tempo segments required")
    last = -1
    for segment in segments:
        if (not isinstance(segment, dict) or not finite(segment.get("start_beats"))
                or segment["start_beats"] < 0
                or segment["start_beats"] <= last or not finite(segment.get("bpm"))
                or segment["bpm"] <= 0):
            raise ValueError("tempo segments must have increasing nonnegative beats and positive BPM")
        last = segment["start_beats"]

    song = Live.Application.get_application().get_document()
    before = {"current_song_time": float(song.current_song_time),
              "is_playing": bool(song.is_playing), "record_mode": bool(song.record_mode),
              "session_record": bool(song.session_record)}
    expected = None
    for segment in segments:
        if segment["start_beats"] <= before["current_song_time"]:
            expected = segment["bpm"]
    actual = float(song.tempo)
    tempo_ok = None if expected is None else abs(actual - expected) <= .001
    report = {"status": "preview" if preview else "ok", "preview": preview,
              "write_outcome": "not_written",
              "would_add": [], "added": [], "skipped": [], "conflicts": [],
              "created_unlabelled": [], "transport_before": before,
              "tempo_check": {"ok": tempo_ok, "verified": tempo_ok is True,
                              "status": ("outside_coverage" if expected is None else
                                         "matched" if tempo_ok else "mismatch"),
                              "expected_bpm": expected, "live_bpm": actual,
                              "coverage_start_beats": segments[0]["start_beats"],
                              "scope": "current playhead only; full automation not verified"}}
    report["writes_enabled"] = False
    blockers = ["Film locator writes are permanently disabled; no override"]
    if any(before[key] for key in ("is_playing", "record_mode", "session_record")):
        blockers.append("Live must be stopped with arrangement and session recording disabled")
    if report["tempo_check"]["ok"] is False:
        blockers.append("Live tempo at the current playhead disagrees with the supplied tempo plan")
    if not confirm_tempo:
        blockers.append("confirm_tempo=True is required after checking the full tempo assumption")
    report["write_blockers"] = blockers

    existing = list(song.cue_points)
    for cue in cues:
        beat, tolerance = cue["start_beats"], cue["tolerance_beats"]
        owned = [c for c in existing if c.name == cue["locator_tag"]
                 or c.name.startswith(cue["locator_tag"] + " ")]
        near = [c for c in existing if abs(float(c.time) - beat) <= tolerance]
        matching = [c for c in owned if c.name == cue["locator_name"]
                    and abs(float(c.time) - beat) <= tolerance]
        overlapping_plan = [other for other in cues if other is not cue
                            and abs(other["start_beats"] - beat)
                            <= max(tolerance, other["tolerance_beats"])]
        if len(matching) == 1 and len(owned) == 1 and len(near) == 1 and not overlapping_plan:
            report["skipped"].append({"id": cue["id"], "name": matching[0].name,
                                      "time_beats": float(matching[0].time)})
        elif owned or near or overlapping_plan:
            report["conflicts"].append({"id": cue["id"], "time_beats": beat,
                                        "reason": "existing/stale locator or planned cue collision"})
        else:
            report["would_add"].append(cue)
    if report["conflicts"]:
        report["status"] = "conflict"
    report["transport_after"] = dict(before)
    report["transport_restored"] = True  # read-only; no transport mutation
    return report


def film_cues_step(c_instance, job_id=None, cancel=False, **_):
    """Retired job endpoint: refuse all advancement, including cancellation."""
    return {"status": "refused", "job_id": job_id, "writes_enabled": False,
            "write_outcome": "unknown", "transport_restored": None,
            "error": "Film locator jobs disabled; no job was advanced or cancelled.",
            "warning": "Inspect Live manually for any effects from earlier software; no restoration claimed."}
