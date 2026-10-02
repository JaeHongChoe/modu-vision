"""Korean project, folder, file and label names survive a process whose default text encoding is not UTF-8.

Covered: the project and folder listing (project.json, metadata ledger, adjacent LabelMe), the Studio overlay and its
mask classes, an imported mask manifest, and a DICOM view receipt (the decoder is replaced, the receipt write is real).

Windows opens text files in the ANSI code page (cp1252 on an English install) unless the code names an encoding,
while the app writes its JSON as UTF-8 with Korean kept as is. The scenario runs in a child process whose locale
encoding is US-ASCII (the nearest non-UTF-8 default available on every POSIX host), so any read or write that relies
on the default fails the same way it fails on Windows. The hosted Windows run executes this with its own code page.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

CHILD = 'MV_TEXT_ENCODING_CHILD'


def _scenario(tmp_path: Path) -> None:
    import locale
    from fastapi.testclient import TestClient
    import backend.main as main
    if os.name != 'nt':
        assert locale.getpreferredencoding(False).upper().replace('-', '') in ('USASCII', 'ASCII', 'ANSI_X3.41968'), \
            'the child must run without a UTF-8 default'
    source = tmp_path / '원본 데이터'
    source.mkdir(parents=True)
    for index in range(4):
        image = source / f'검사_{index}.png'
        Image.new('RGB', (16, 16), 'white').save(image)
        image.with_suffix('.json').write_text(json.dumps({
            'imagePath': image.name, 'imageWidth': 16, 'imageHeight': 16,
            'shapes': [{'label': '스크래치', 'shape_type': 'rectangle', 'points': [[1, 1], [5, 5]]}]}, ensure_ascii=False),
            encoding='utf-8')
    app = main.create_app(project_dir=str(tmp_path / '프로젝트'))
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        assert client.post('/api/project/create', json={'name': '검사 프로젝트', 'task': 'detection'}).status_code == 200
        assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
        for _ in range(2):  # the second listing reads the metadata ledger the first one wrote
            gallery = client.get('/api/dataset/images', params={'folder_path': str(source), 'task': 'detection', 'limit': 50})
            assert gallery.status_code == 200, gallery.text
            rows = gallery.json()['items']
            assert len(rows) == 4 and all('스크래치' in row['labels'] for row in rows), rows
        statistics = client.get('/api/dataset/metadata/statistics', params={'folder_path': str(source)})
        assert statistics.status_code == 200, statistics.text
    _engine_sites(tmp_path, source)


def _engine_sites(tmp_path: Path, source: Path) -> None:
    """The Studio overlay and mask classes, an imported mask manifest and a DICOM view receipt, with Korean text."""
    import numpy as np
    from backend.engine import dicom_input
    from backend.engine.annotation_storage import dataset_annotation_dir
    from backend.engine.grouped_dataset_views import _annotations, _mask_class_mapping
    from backend.engine.mask_exchange import load_mask_bundle
    image = source / '검사_0.png'
    studio = dataset_annotation_dir(image.parent) / '검사_0.json'
    studio.parent.mkdir(parents=True, exist_ok=True)
    studio.write_text(json.dumps({'annotations': [{'type': 'bbox', 'label': '찍힘', 'bbox': [1, 1, 4, 4]}],
                                  'mask_classes': [{'id': 1, 'name': '찍힘'}]}, ensure_ascii=False), encoding='utf-8')
    annotations, _mask = _annotations(source, image)
    assert annotations[0]['label'] == '찍힘' and _mask_class_mapping(image) == {1: '찍힘'}
    bundle = tmp_path / '마스크 묶음'
    bundle.mkdir()
    Image.fromarray(np.ones((16, 16), np.uint8), mode='L').save(bundle / '검사_1.png')
    (bundle / 'mask_manifest.json').write_text(json.dumps({
        'schema_version': 1, 'classes': [{'id': 0, 'name': '배경', 'color': '#000000'}, {'id': 1, 'name': '긁힘', 'color': '#ff0000'}],
        'images': [{'file_name': '검사_1.png', 'mask_file': '검사_1.png', 'width': 16, 'height': 16}]}, ensure_ascii=False), encoding='utf-8')
    assert load_mask_bundle(bundle)[0]['annotations'][0]['label'] == '긁힘'
    scan = source / '단면 촬영.dcm'
    scan.write_bytes(b'not read: the decoder is replaced below')
    dicom_input.read_dicom = lambda path, **options: (Image.new('RGB', (8, 8)), {'source_path': str(path), 'modality': '단면'})
    receipt = dicom_input.normalized_view(scan, tmp_path / '보기')
    assert json.loads(Path(receipt['view_path']).with_suffix('.json').read_text(encoding='utf-8'))['modality'] == '단면'


def test_korean_names_survive_a_default_encoding_that_is_not_utf8(tmp_path):
    if os.environ.get(CHILD):
        _scenario(tmp_path)
        return
    environment = {**os.environ, CHILD: '1', 'PYTHONUTF8': '0', 'PYTHONCOERCECLOCALE': '0', 'PYTHONIOENCODING': 'utf-8'}
    if os.name != 'nt':
        environment.update(LC_ALL='C', LANG='C')
    environment.pop('LC_CTYPE', None)
    result = subprocess.run(
        [sys.executable, '-X', 'utf8=0', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--basetemp', str(tmp_path / 'child'),
         f'{__file__}::test_korean_names_survive_a_default_encoding_that_is_not_utf8'],
        cwd=Path(__file__).resolve().parents[2], env=environment, capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=300)
    if result.returncode != 0:
        pytest.fail(f'the scenario failed without a UTF-8 default:\n{result.stdout[-6000:]}\n{result.stderr[-2000:]}')


_SUBPROCESS = {'run', 'Popen', 'check_output', 'check_call', 'call'}


def _default_encoding_calls(tree):
    """Text-mode file I/O and text subprocess pipes in this tree that would use the platform default encoding."""
    import ast

    def mode(node, index, default):
        if len(node.args) > index and isinstance(node.args[index], ast.Constant):
            return node.args[index].value
        return next((k.value.value for k in node.keywords if k.arg == 'mode' and isinstance(k.value, ast.Constant)), default)

    def textual(value):
        return isinstance(value, str) and 'b' not in value

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {k.arg: k.value for k in node.keywords}
        if 'encoding' in keywords or None in keywords:  # an explicit encoding, or **kwargs the guard cannot see into
            continue
        attr = node.func.attr if isinstance(node.func, ast.Attribute) else None
        name = node.func.id if isinstance(node.func, ast.Name) else None
        owner = node.func.value.id if attr and isinstance(node.func.value, ast.Name) else None
        pipe = [keywords[k] for k in ('text', 'universal_newlines') if k in keywords] if (attr or name) in _SUBPROCESS else []
        if pipe and not (isinstance(pipe[0], ast.Constant) and pipe[0].value is False):
            yield node.lineno, 'text pipe'  # text=True, or a value the guard cannot prove false
        elif (attr == 'read_text' and not node.args) or (attr == 'write_text' and len(node.args) < 2):
            yield node.lineno, attr
        elif name == 'open' or attr == 'fdopen' or (attr == 'open' and owner in ('io', 'codecs')):
            if textual(mode(node, 1, 'r')):
                yield node.lineno, f'{owner + "." if owner else ""}{name or attr}'
        elif attr in ('NamedTemporaryFile', 'TemporaryFile', 'SpooledTemporaryFile'):
            if textual(mode(node, 0, 'w+b')):
                yield node.lineno, attr
        elif attr == 'open' and owner not in ('Image', 'zipfile', 'tarfile', 'gzip', 'bz2', 'lzma', 'webbrowser', 'os'):
            # Path.open: mode positional or keyword; no mode at all reads text
            if not node.args and textual(mode(node, 99, 'r')):
                yield node.lineno, 'Path.open'
            elif node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) \
                    and set(node.args[0].value) <= set('rwaxt+') and textual(node.args[0].value):
                yield node.lineno, 'Path.open'
        elif (attr or name) in ('TextIOWrapper', 'FileHandler', 'RotatingFileHandler', 'TimedRotatingFileHandler'):
            yield node.lineno, attr or name
        elif attr == 'popen' and owner == 'os':
            yield node.lineno, 'os.popen (always the locale; use subprocess with an encoding)'


def test_backend_code_names_an_encoding_for_every_text_file_and_text_pipe():
    """Windows decodes text with the ANSI code page unless the code says otherwise; production code always says.
    Covers the backend and the build/release scripts (one of them ships inside the frozen backend)."""
    import ast
    repository = Path(__file__).resolve().parents[2]
    found = []
    for base in ('backend', 'scripts'):
        for path in sorted((repository / base).rglob('*.py')):
            if 'tests' in path.relative_to(repository).parts:
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'))
            found += [f'{path.relative_to(repository).as_posix()}:{line} {what}' for line, what in _default_encoding_calls(tree)]
    assert found == [], 'name an encoding (UTF-8 for app files; the locale with errors="replace" for OS tool output):\n' + '\n'.join(found)


def test_the_guard_recognises_each_default_encoding_form():
    import ast
    flagged = ["p.read_text()", "p.write_text(s)", "open(p)", "open(p, 'w')", "os.fdopen(d, 'w')", "p.open('a')", "p.open()",
               "p.open(mode='w')", "io.open(p)", "codecs.open(p, 'r')", "tempfile.NamedTemporaryFile('w')",
               "subprocess.run(c, text=True)", "subprocess.Popen(c, universal_newlines=flag)", "io.TextIOWrapper(raw)",
               "logging.FileHandler(p)", "os.popen(c)"]
    clean = ["p.read_text(encoding='utf-8')", "open(p, 'rb')", "p.open('rb')", "p.open(mode='rb')", "Image.open(p)",
             "zipfile.ZipFile(p).open(name)", "subprocess.run(c, text=True, encoding='utf-8')", "subprocess.run(c)",
             "tempfile.NamedTemporaryFile()", "processor(images=x, text=t)", "open(p, **options)"]
    source = '\n'.join(flagged + clean)
    assert [line for line, _what in _default_encoding_calls(ast.parse(source))] == list(range(1, len(flagged) + 1))


def test_source_label_text_decodes_utf8_bom_and_only_this_machines_code_page(tmp_path, monkeypatch):
    import locale
    from backend.engine.source_text import SourceTextError, read_source_text
    utf8 = tmp_path / 'classes.txt'
    utf8.write_bytes('양품\n불량\n'.encode('utf-8'))
    assert read_source_text(utf8) == '양품\n불량\n'
    bom = tmp_path / 'bom.json'
    bom.write_bytes(b'\xef\xbb\xbf' + '{"label": "찍힘"}'.encode('utf-8'))
    assert json.loads(read_source_text(bom)) == {'label': '찍힘'}
    ansi = tmp_path / 'ansi.txt'
    ansi.write_bytes('양품\n불량\n'.encode('cp949'))  # saved by a tool on Korean Windows
    monkeypatch.setattr(locale, 'getpreferredencoding', lambda do_setlocale=True: 'cp949')
    assert read_source_text(ansi) == '양품\n불량\n', 'what the platform default read on that machine still reads'
    monkeypatch.setattr(locale, 'getpreferredencoding', lambda do_setlocale=True: 'UTF-8')
    with pytest.raises(SourceTextError, match='save it as UTF-8'):
        read_source_text(ansi)  # no code page is guessed where the platform would not have used it


def test_a_transient_failure_beside_a_size_less_labelme_file_is_raised_not_unlabelled(tmp_path, monkeypatch):
    from PIL import Image as PILImage
    from backend.engine import grouped_dataset_views
    source = tmp_path / 'source'
    source.mkdir()
    image = source / 'part.png'
    Image.new('RGB', (8, 8)).save(image)
    image.with_suffix('.json').write_text(json.dumps({'shapes': [{'label': '찍힘', 'shape_type': 'rectangle',
                                                                  'points': [[1, 1], [4, 4]]}]}), encoding='utf-8')

    def locked(path):
        raise PermissionError(32, 'The process cannot access the file because it is being used by another process')

    monkeypatch.setattr(grouped_dataset_views, 'open_source_image', locked)
    with pytest.raises(PermissionError):
        grouped_dataset_views._annotations(source, image)  # training must not get an empty mask for a locked file

    def undecodable(path):
        raise PILImage.UnidentifiedImageError('cannot identify image file')

    monkeypatch.setattr(grouped_dataset_views, 'open_source_image', undecodable)
    assert grouped_dataset_views._annotations(source, image) == ([], None)
