"""Offline ALS copies: exact preservation, refusal before output, no Live calls."""
import gzip
import hashlib
import xml.etree.ElementTree as ET

import pytest

from tests.test_film_cues import sheet


# Locator structure observed in a saved Live 12.3.6 validation set; 12.4.5
# empty-container spelling observed in the actual Ch5 set. No media is embedded.
XML = b'''<?xml version="1.0" encoding="UTF-8"?>\r
<Ableton MajorVersion="5" MinorVersion="12.0_12402" SchemaChangeCount="5" Creator="Ableton Live 12.4.5">
 <LiveSet><NextPointeeId Value="9000"/><Tracks><AudioTrack Id="1"><Opaque Value="keep &amp; preserve"/><FileRef><RelativePath Value="Samples/voice.wav"/></FileRef></AudioTrack></Tracks><MainTrack/>
 <!-- preserve comment and whitespace -->
 <Locators><Locators /></Locators><Annotation Value="Original"/>
 </LiveSet></Ableton>'''


def export(source, output, **kwargs):
    from ableton_mcp.film_cue_set import export_film_cue_set
    return export_film_cue_set(str(source), str(output), sheet((24, 25)),
                               tempo_bpm=120, confirm_tempo=True, **kwargs)


@pytest.fixture
def files(tmp_path):
    source, output = tmp_path / "original.als", tmp_path / "cued.als"
    source.write_bytes(gzip.compress(XML, mtime=42))
    return source, output


def test_inserts_adjacent_offline_without_changing_source_or_unrelated_xml(files):
    source, output = files
    original = source.read_bytes()
    receipt = export(source, output)
    assert source.read_bytes() == original
    result = gzip.decompress(output.read_bytes())
    root = ET.fromstring(result)
    locators = root.findall('LiveSet/Locators/Locators/Locator')
    assert [float(x.find('Time').get('Value')) for x in locators] == [2, 25/12]
    assert [x.get('Id') for x in locators] == ['0', '1']
    assert [x.find('Name').get('Value') for x in locators] == [
        '[film:film:marker:24] Entrance', '[film:film:marker:25] Entrance']
    prefix, suffix = XML.split(b'<Locators />')
    assert result.startswith(prefix) and result.endswith(suffix)
    assert receipt['source_sha256'] == hashlib.sha256(original).hexdigest()
    assert receipt['output_sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert receipt['unrelated_xml_preserved'] is True
    assert receipt['live_set_opened'] is False


def test_existing_locator_bytes_and_local_ids_preserved(files):
    source, output = files
    existing = b'<Locator Id="8"><LomId Value="0"/><Time Value="40"/><Name Value="Human &amp; cue"/><Annotation Value="keep"/><IsSongStart Value="true"/></Locator>'
    source.write_bytes(gzip.compress(XML.replace(b'<Locators />', b'<Locators>\n'+existing+b'\n</Locators>')))
    export(source, output)
    result = gzip.decompress(output.read_bytes())
    assert existing in result
    assert [x.get('Id') for x in ET.fromstring(result).findall('LiveSet/Locators/Locators/Locator')] == ['8', '9', '10']


@pytest.mark.parametrize('collision', ['position', 'owned_tag'])
def test_locator_collisions_refuse_before_any_output(files, collision):
    source, output = files
    time, name = ('2', 'Human') if collision == 'position' else ('80', '[film:film:marker:24] Old')
    item = f'<Locator Id="0"><LomId Value="0"/><Time Value="{time}"/><Name Value="{name}"/><Annotation Value=""/><IsSongStart Value="false"/></Locator>'.encode()
    source.write_bytes(gzip.compress(XML.replace(b'<Locators />', b'<Locators>'+item+b'</Locators>')))
    before = source.read_bytes()
    with pytest.raises(ValueError, match='collision'):
        export(source, output)
    assert not output.exists() and source.read_bytes() == before


@pytest.mark.parametrize('kind', ['source', 'existing', 'repeat', 'other_parent'])
def test_never_overwrites_or_relocates_relative_assets(files, tmp_path, kind):
    source, output = files
    if kind == 'source':
        output = source
    elif kind == 'existing':
        output.write_bytes(b'owned by someone else')
    elif kind == 'repeat':
        export(source, output)
    else:
        (tmp_path / 'elsewhere').mkdir()
        output = tmp_path / 'elsewhere' / 'copy.als'
    before = output.read_bytes() if output.exists() else None
    with pytest.raises(ValueError):
        export(source, output)
    assert (output.read_bytes() if output.exists() else None) == before


@pytest.mark.parametrize('bad_xml', [
    XML.replace(b'12.0_12402', b'13.0_99999'),
    XML.replace(b'<Locators />', b'<Locators /><Locators />'),
    XML.replace(b'<Locators />', b'<Unknown />'),
    XML.replace(b'<MainTrack/>', b''),
    XML.replace(b'<Locators />', b'<Locators><Locator Id="0"/></Locators>'),
    XML.replace(b'<Ableton ', b'<!DOCTYPE Ableton [<!ENTITY x "boom">]><Ableton '),
    XML.replace(b'</Ableton>', b''),
])
def test_unsupported_or_malformed_xml_refused(files, bad_xml):
    source, output = files
    source.write_bytes(gzip.compress(bad_xml))
    with pytest.raises(ValueError):
        export(source, output)
    assert not output.exists()


def test_gzip_expansion_limit_refuses_before_output(files, monkeypatch):
    from ableton_mcp import film_cue_set
    monkeypatch.setattr(film_cue_set, 'MAX_XML_BYTES', 128)
    with pytest.raises(ValueError, match='limit'):
        export(*files)
    assert not files[1].exists()


def test_tempo_confirmation_required_without_inference(files):
    from ableton_mcp.film_cue_set import export_film_cue_set
    with pytest.raises(ValueError, match='confirm_tempo'):
        export_film_cue_set(*(str(p) for p in files), sheet(), tempo_bpm=120)
    assert not files[1].exists()


@pytest.mark.asyncio
async def test_tool_exports_offline_and_registers(files, monkeypatch):
    from ableton_mcp.server import build_server
    from ableton_mcp import bridge_client
    monkeypatch.setattr(bridge_client, 'get_bridge_client', lambda: pytest.fail('offline tool connected'))
    mcp = build_server()
    assert 'film_cues_export_set' in {t.name for t in await mcp.list_tools()}
    _, result = await mcp.call_tool('film_cues_export_set', {
        'source_set_path': str(files[0]), 'output_set_path': str(files[1]),
        'cue_sheet': sheet((24, 25)), 'tempo_bpm': 120, 'confirm_tempo': True,
    })
    assert result['status'] == 'ok' and result['locators_added'] == 2


def test_publish_race_preserves_other_writers_output(files, monkeypatch):
    from ableton_mcp import film_cue_set
    original_link = film_cue_set.os.link
    def race(temporary, output):
        output.write_bytes(b'other writer won')
        original_link(temporary, output)
    monkeypatch.setattr(film_cue_set.os, 'link', race)
    with pytest.raises(ValueError, match='appeared'):
        export(*files)
    assert files[1].read_bytes() == b'other writer won'
    assert list(files[0].parent.glob('.film-cues-*')) == []


def test_changed_source_is_not_published(files, monkeypatch):
    from ableton_mcp import film_cue_set
    real_read = film_cue_set._read
    reads = 0
    def change(path):
        nonlocal reads
        if path == files[0]:
            reads += 1
            if reads == 2:
                path.write_bytes(gzip.compress(XML.replace(b'Original', b'Changed by owner')))
        return real_read(path)
    monkeypatch.setattr(film_cue_set, '_read', change)
    with pytest.raises(ValueError, match='Source changed'):
        export(*files)
    assert not files[1].exists()
    assert b'Changed by owner' in gzip.decompress(files[0].read_bytes())
    assert list(files[0].parent.glob('.film-cues-*')) == []


@pytest.mark.parametrize('data', [b'not gzip', gzip.compress(XML)[:-6], b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff\x07'])
def test_corrupt_gzip_is_a_structured_refusal(files, data):
    files[0].write_bytes(data)
    with pytest.raises(ValueError, match='gzip'):
        export(*files)
    assert not files[1].exists()


def test_compressed_size_bound(files, monkeypatch):
    from ableton_mcp import film_cue_set
    monkeypatch.setattr(film_cue_set, 'MAX_COMPRESSED_BYTES', 16)
    with pytest.raises(ValueError, match='limit'):
        export(*files)
    assert not files[1].exists()


def test_explicit_tempo_map_and_xml_escaped_names(files):
    from ableton_mcp.film_cue_set import export_film_cue_set
    data = sheet((24, 48))
    data['cues'][0]['name'] = 'Jim & "the <moon>"'
    result = export_film_cue_set(*(str(p) for p in files), data,
        tempo_map=[{'start_seconds': 0, 'bpm': 120}, {'start_seconds': 1, 'bpm': 60}],
        arrangement_start_beats=8, confirm_tempo=True)
    assert [c['start_beats'] for c in result['plan']['cues']] == [10, 11]
    root = ET.fromstring(gzip.decompress(files[1].read_bytes()))
    assert root.find('LiveSet/Locators/Locators/Locator/Name').get('Value') == '[film:film:marker:24] Jim & "the <moon>"'


def test_illegal_xml_character_never_creates_output(files):
    from ableton_mcp.film_cue_set import export_film_cue_set
    data = sheet()
    data['cues'][0]['name'] = 'bad\x00name'
    with pytest.raises(ValueError):
        export_film_cue_set(*(str(p) for p in files), data, tempo_bpm=120, confirm_tempo=True)
    assert not files[1].exists()


@pytest.mark.parametrize('phase', ['before_link', 'after_link'])
def test_temporary_replacement_cannot_publish_false_receipt_or_delete_replacement(files, monkeypatch, phase):
    from ableton_mcp import film_cue_set
    original_link = film_cue_set.os.link
    foreign = b'not ours - preserve this file'
    replacement_path = None
    replacement_blocked = False
    def replace(temporary):
        nonlocal replacement_path, replacement_blocked
        try:
            temporary.unlink()
        except PermissionError:
            replacement_blocked = True
            return
        temporary.write_bytes(foreign)
        replacement_path = temporary
    def attack(temporary, output):
        if phase == 'before_link':
            replace(temporary)
        original_link(temporary, output)
        if phase == 'after_link':
            replace(temporary)
    monkeypatch.setattr(film_cue_set.os, 'link', attack)
    try:
        result = export(*files)
    except (OSError, ValueError):
        result = None
    if replacement_blocked:
        assert replacement_path is None
    elif replacement_path is not None and replacement_path.exists():
        assert replacement_path.read_bytes() == foreign
    else:
        pytest.fail('Cleanup deleted the replacement at the temporary path')
    if result is not None:
        assert result['output_sha256'] == hashlib.sha256(files[1].read_bytes()).hexdigest()
        assert gzip.decompress(files[1].read_bytes()).startswith(b'<?xml')


def test_cleanup_keeps_replacement_created_after_owned_handle_closes(files, monkeypatch):
    from ableton_mcp import film_cue_set
    cleanup = film_cue_set._cleanup_owned_temp
    replaced = []
    def attack(path, identity):
        path.unlink()
        path.write_bytes(b'foreign replacement')
        replaced.append(path)
        return cleanup(path, identity)
    monkeypatch.setattr(film_cue_set, '_cleanup_owned_temp', attack)
    receipt = export(*files)
    assert replaced[0].read_bytes() == b'foreign replacement'
    assert receipt['temporary_retained'] == str(replaced[0])
    assert receipt['output_sha256'] == hashlib.sha256(files[1].read_bytes()).hexdigest()


@pytest.mark.parametrize('publish', [False, True])
def test_reviewer_rename_replacement_then_link_or_collision(files, monkeypatch, publish):
    from ableton_mcp import film_cue_set
    real_link = film_cue_set.os.link
    foreign_paths = []
    def race(temporary, output):
        temporary.rename(temporary.with_suffix('.saved'))
        temporary.write_bytes(b'independent replacement, not ALS')
        foreign_paths.append(temporary)
        if publish:
            real_link(temporary, output)
        else:
            raise FileExistsError('simulated publication collision')
    monkeypatch.setattr(film_cue_set.os, 'link', race)
    with pytest.raises((OSError, ValueError)):
        export(*files)
    # On Windows the pinned open handle blocks rename before a foreign file
    # can be created. If replacement succeeds, neither path may be deleted.
    for path in foreign_paths:
        assert path.read_bytes() == b'independent replacement, not ALS'
    if files[1].exists():
        assert files[1].read_bytes() == b'independent replacement, not ALS'


def test_changed_payload_after_link_cannot_return_success(files, monkeypatch):
    from ableton_mcp import film_cue_set
    real_link = film_cue_set.os.link
    def corrupt(temporary, output):
        real_link(temporary, output)
        output.write_bytes(b'concurrent content change')
    monkeypatch.setattr(film_cue_set.os, 'link', corrupt)
    with pytest.raises(ValueError, match='Published output'):
        export(*files)
    assert files[1].read_bytes() == b'concurrent content change'
