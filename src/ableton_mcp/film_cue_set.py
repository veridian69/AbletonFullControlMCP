"""Conservative offline ALS locator-copy export; never imports or calls Live."""
from __future__ import annotations

import gzip
import hashlib
import io
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any
import xml.etree.ElementTree as ET
import zlib
from xml.parsers import expat
from xml.sax.saxutils import quoteattr

from .film_cues import plan_film_cues

MAX_COMPRESSED_BYTES = 16 * 1024 * 1024
MAX_XML_BYTES = 64 * 1024 * 1024
# Observed modern locator profile and actual Ch5 saved-set container profile.
_PROFILES = {('5', '12.0_12300', '1', 'Ableton Live 12.3.6'),
             ('5', '12.0_12402', '5', 'Ableton Live 12.4.5')}
_FIELDS = ('LomId', 'Time', 'Name', 'Annotation', 'IsSongStart')


def _read(path: Path) -> bytes:
    with path.open('rb') as stream:
        data = stream.read(MAX_COMPRESSED_BYTES + 1)
    if len(data) > MAX_COMPRESSED_BYTES:
        raise ValueError('Compressed ALS exceeds size limit')
    return data


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _cleanup_owned_temp(path: Path, identity: tuple[int, int]) -> bool:
    """Delete only the verified object, never an unchecked path replacement.

    Windows disposition is bound to the opened handle, so a pathname race
    after identity verification cannot redirect deletion. Elsewhere retain the
    temporary: portable stat-then-unlink is not an atomic ownership check.
    """
    if os.name != 'nt':
        return False
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    dispose = kernel.SetFileInformationByHandle
    dispose.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    dispose.restype = wintypes.BOOL
    # READ | DELETE, share READ/WRITE/DELETE, OPEN_EXISTING, OPEN_REPARSE_POINT.
    handle = create(str(path), 0x80010000, 7, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        return ctypes.get_last_error() in (2, 3)  # already absent
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except OSError:
        close = kernel.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close(handle)
        return False
    try:
        if _identity(os.fstat(descriptor)) != identity:
            return False
        delete = ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile is BOOLEAN, not BOOL
        return bool(dispose(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete)))
    finally:
        os.close(descriptor)  # completes deletion of the handle-verified link only


def _paths(source_set_path: str, output_set_path: str) -> tuple[Path, Path]:
    source, output = Path(source_set_path), Path(output_set_path)
    for path in (source, output):
        if not path.is_absolute() or path.suffix.lower() != '.als':
            raise ValueError('Source and output must be absolute .als paths')
        if ':' in path.name or path.name.rstrip(' .') != path.name or path.is_reserved():
            raise ValueError('Unsupported output/source filename')
        # Refuse symlinks and Windows reparse points, including parent junctions.
        for part in (path, *path.parents):
            if os.path.lexists(part):
                info = part.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('Symlink/reparse paths are not supported')
    if not source.is_file() or not stat.S_ISREG(source.stat().st_mode):
        raise ValueError('Source must be an existing regular ALS file')
    source = source.resolve(strict=True)
    if output.parent.resolve(strict=True) != source.parent:
        raise ValueError('Output must have the same parent directory; asset relocation is not supported')
    output = source.parent / output.name
    if os.path.lexists(output):
        raise ValueError('Output already exists; no overwrite or repeat replacement permitted')
    return source, output


def _inspect(xml: bytes) -> tuple[ET.Element, int, int, int, bool]:
    """Parse structurally, returning exact byte offsets of the inner container."""
    if len(xml) > MAX_XML_BYTES:
        raise ValueError('Uncompressed ALS exceeds size limit')
    try:
        xml.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('Only UTF-8 ALS XML is supported') from exc
    if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper() or b'\x00' in xml:
        raise ValueError('DTD, entities and non-UTF-8 XML are unsupported')
    declaration = re.match(br'<\?xml\s+[^?]*encoding=["\']([^"\']+)', xml)
    if declaration and declaration[1].lower() != b'utf-8':
        raise ValueError('Only UTF-8 XML declarations are supported')
    parser = expat.ParserCreate('utf-8')
    stack, spans = [], []
    target = ['Ableton', 'LiveSet', 'Locators', 'Locators']

    def start(name, attrs):
        stack.append(name)
        if len(stack) > 256:
            raise ValueError('XML nesting exceeds safety limit')
        if stack == target:
            offset = parser.CurrentByteIndex
            opening = re.match(br'<Locators\s*(/?)>', xml[offset:offset + 100])
            if attrs or opening is None:
                raise ValueError('Unsupported locator container opening')
            spans.append([offset, offset + opening.end(), None, bool(opening[1])])

    def end(name):
        if stack == target:
            spans[-1][2] = parser.CurrentByteIndex
        stack.pop()

    parser.StartElementHandler, parser.EndElementHandler = start, end
    try:
        parser.Parse(xml, True)
        root = ET.fromstring(xml)
    except (expat.ExpatError, ET.ParseError) as exc:
        raise ValueError('Invalid ALS XML') from exc
    profile = tuple(root.get(key) for key in ('MajorVersion', 'MinorVersion', 'SchemaChangeCount', 'Creator'))
    if root.tag != 'Ableton' or profile not in _PROFILES:
        raise ValueError('Unsupported ALS version/profile; no speculative schema conversion')
    sets = root.findall('LiveSet')
    if len(sets) != 1:
        raise ValueError('Expected exactly one LiveSet')
    live_set = sets[0]
    if any(len(live_set.findall(tag)) != 1 for tag in ('Tracks', 'MainTrack', 'Locators')):
        raise ValueError('Unexpected LiveSet topology')
    outer = live_set.find('Locators')
    if outer.attrib or len(outer) != 1 or outer[0].tag != 'Locators' or len(spans) != 1:
        raise ValueError('Expected one nested locator container')
    container = outer[0]
    ids = set()
    for item in container:
        if (item.tag != 'Locator' or set(item.attrib) != {'Id'}
                or not re.fullmatch(r'0|[1-9][0-9]*', item.get('Id', ''))
                or [child.tag for child in item] != list(_FIELDS)
                or any(set(child.attrib) != {'Value'} or len(child) for child in item)):
            raise ValueError('Unsupported locator structure')
        identity = int(item.get('Id'))
        if identity in ids or identity >= 2**31 - 1:
            raise ValueError('Duplicate or out-of-range locator ID')
        ids.add(identity)
        try:
            beat = float(item.find('Time').get('Value'))
        except ValueError as exc:
            raise ValueError('Invalid locator time') from exc
        if (not math.isfinite(beat) or beat < 0 or item.find('LomId').get('Value') != '0'
                or item.find('IsSongStart').get('Value') not in ('true', 'false')):
            raise ValueError('Unsupported locator field values')
    start_byte, opening_end, close_start, empty = spans[0]
    if not empty and xml[close_start:close_start + 11] != b'</Locators>':
        raise ValueError('Unsupported locator closing tag')
    return container, start_byte, opening_end, close_start, empty


def export_film_cue_set(
    source_set_path: str,
    output_set_path: str,
    cue_sheet: dict[str, Any],
    tempo_bpm: float | None = None,
    tempo_map: list[dict[str, Any]] | None = None,
    arrangement_start_beats: float = 0.0,
    cue_namespace: str = 'film',
    confirm_tempo: bool = False,
) -> dict[str, Any]:
    """Create a new same-directory ALS, preserving all non-locator XML bytes.

    Existing outputs always refuse, including identical repeats. No Live access,
    tempo edits, asset relocation, source overwrite or project opening occurs.
    """
    if confirm_tempo is not True:
        raise ValueError('confirm_tempo=True required: full caller-supplied map must match the saved set')
    plan = plan_film_cues(cue_sheet, tempo_bpm, tempo_map, arrangement_start_beats, cue_namespace)
    source, output = _paths(source_set_path, output_set_path)
    original = _read(source)
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(original), mode='rb') as zipped:
            xml = zipped.read(MAX_XML_BYTES + 1)
    except (OSError, EOFError, zlib.error) as exc:
        raise ValueError('Invalid gzip ALS') from exc
    container, start, opening_end, closing, empty = _inspect(xml)
    existing = [(float(c.find('Time').get('Value')), c.find('Name').get('Value')) for c in container]
    for cue in plan['cues']:
        if any(abs(beat - cue['start_beats']) <= cue['tolerance_beats']
               or name == cue['locator_tag'] or name.startswith(cue['locator_tag'] + ' ')
               for beat, name in existing):
            raise ValueError('Locator collision: existing position or owned tag; nothing written')
    next_id = max((int(c.get('Id')) for c in container), default=-1) + 1
    if next_id + len(plan['cues']) >= 2**31:
        raise ValueError('Locator IDs exhausted')
    additions = []
    for index, cue in enumerate(plan['cues']):
        name = quoteattr(cue['locator_name'])
        additions.append(f'<Locator Id="{next_id + index}"><LomId Value="0" />'
                         f'<Time Value="{cue["start_beats"]!r}" /><Name Value={name} />'
                         '<Annotation Value="" /><IsSongStart Value="false" /></Locator>')
    inserted = ''.join(additions).encode('utf-8')
    if empty:
        changed = xml[:start] + b'<Locators>' + inserted + b'</Locators>' + xml[opening_end:]
    else:
        changed = xml[:closing] + inserted + xml[closing:]
    # Re-parse before any file creation; catches invalid XML characters in labels.
    verified, *_ = _inspect(changed)
    if len(verified) != len(container) + len(additions):
        raise ValueError('Locator count verification failed')
    payload = gzip.compress(changed, mtime=0)
    if len(payload) > MAX_COMPRESSED_BYTES:
        raise ValueError('Output exceeds compressed size limit')
    temporary = None
    temporary_identity = None
    temporary_retained = None
    try:
        # A verified temporary is atomically linked to a NEW destination. Unlike
        # replace/rename-overwrite this fails if another writer wins the name.
        with tempfile.NamedTemporaryFile(prefix='.film-cues-', suffix='.tmp', dir=source.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            temporary_identity = _identity(os.fstat(stream.fileno()))
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            # Keep the original handle open through publication. On Windows this
            # also denies deletion/replacement while validating and linking.
            if (_identity(temporary.lstat()) != temporary_identity or _read(temporary) != payload):
                raise ValueError('Temporary copy identity/content verification failed')
            if _read(source) != original:
                raise ValueError('Source changed during export; nothing published')
            os.link(temporary, output)  # never falls back to overwrite
            if (_identity(output.lstat()) != temporary_identity or _read(output) != payload):
                raise ValueError('Published output identity/content mismatch; inspect output, no success receipt')
    except FileExistsError as exc:
        raise ValueError('Output appeared during export; refusing overwrite') from exc
    finally:
        if temporary is not None and temporary_identity is not None:
            if not _cleanup_owned_temp(temporary, temporary_identity):
                temporary_retained = str(temporary)
    return {'status': 'ok', 'source_set_path': str(source), 'output_set_path': str(output),
            'source_sha256': hashlib.sha256(original).hexdigest(),
            'output_sha256': hashlib.sha256(payload).hexdigest(),
            'source_xml_sha256': hashlib.sha256(xml).hexdigest(),
            'output_xml_sha256': hashlib.sha256(changed).hexdigest(),
            'temporary_retained': temporary_retained,
            'locators_added': len(additions), 'existing_locators_preserved': len(container),
            'unrelated_xml_preserved': True, 'live_set_opened': False, 'plan': plan,
            'warning': 'Saved-set copy only; unsaved Live changes excluded. Load validation remains separate. '
                       'Tempo automation and media availability were not verified.'}
