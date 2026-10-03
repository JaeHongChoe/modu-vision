"""Source annotations of dataset images, as the training importer would read them (S3-01).

For each image of an index build this records what the source's own annotation files say: the format (LabelMe,
COCO, YOLO, folder mask), the label names, and every annotation file that binds the image with its SHA-256, so a revision states
exactly which label bytes it saw. Project overlays (Studio edits) are not source annotations; label sets that combine
them belong to dataset snapshots (S3-02).

Discovery and binding follow ``annotation_formats.source_annotations_for_image``:

- an adjacent LabelMe JSON binds the image named by its ``imagePath``; without one it binds the image with its stem,
  and two images sharing that stem make the binding ambiguous;
- ``annotations.json`` or ``annotations_<split>.json`` (COCO) in the source or the image's folders binds by
  source-relative or document-relative ``file_name`` (or by bare name inside its own split).
  Unsafe names, an unknown category, or a size that differs from the image
  make the document invalid for that image;
- YOLO ``.txt`` labels take their names from the first ``classes.txt``, else the first ``data.yaml``/``dataset.yaml``
  (the importer's precedence); class lists that disagree, blank or missing names and ids outside the list are errors.
- folder masks bind by the segmentation loader's image/mask layout and stem precedence after the standard
  annotation formats; native geometry, grayscale PNG channels and class_map.json are validated without conversion.

Annotation files are read only inside the source (a local desktop may follow links, like the image walk), and paths
are recorded relative to the source. COCO documents and class lists are parsed once per scanner and only their index
is kept; LabelMe documents are read per image and not kept, so memory does not grow with the dataset. Nothing is
guessed: every problem is an explicit ``INVALID_ANNOTATION`` or ``AMBIGUOUS_ANNOTATION``.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Optional

from backend.engine.annotation_formats import safe_name, source_annotation_files, source_coco_image_names, validate_source_coco_binding
from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS
from backend.engine.source_text import decode_source_text

SPLITS = ('train', 'val', 'test')
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class SourceAnnotation:
    format: Optional[str]
    labels: tuple
    files: tuple  # ((source-relative path, sha256), ...)
    error: Optional[str] = None


NONE = SourceAnnotation(None, (), ())


class _Refused(Exception):
    """An annotation problem stated as ``CODE: detail``."""


class SourceAnnotationScanner:
    def __init__(self, source: Path | str, *, follow_links: bool = False, task: Optional[str] = None):
        self.source = Path(source).resolve()
        self.follow_links = follow_links
        self.task = task
        self._real_source = os.path.realpath(self.source)
        self._hash: dict = {}
        self._coco: dict = {}
        self._coco_hash: dict = {}
        self._classes: dict = {}
        self._class_errors: dict = {}
        self._class_hash: dict = {}
        self._consumed: dict = {}
        from backend.engine.source_mask_annotations import SourceMaskScanner
        self._source_masks = SourceMaskScanner(self.source, readable=self._readable, record=self._record)

    def _relative(self, path: Path) -> str:
        try:
            return Path(path).relative_to(self.source).as_posix()
        except ValueError:
            raise _Refused(f'INVALID_ANNOTATION: {Path(path).name} is not inside the source') from None

    def _readable(self, path: Path) -> Path:
        """``path`` when reading it stays inside the source (or links may be followed)."""
        relative = self._relative(path)
        if not self.follow_links:
            real = os.path.realpath(path)
            if os.path.commonpath([real, self._real_source]) != self._real_source:
                raise _Refused(f'INVALID_ANNOTATION: {relative} links outside the source; links are not followed')
        return path

    def _sha(self, path: Path) -> str:
        key = str(path)
        if key not in self._hash:
            hasher = hashlib.sha256()
            with self._readable(path).open('rb') as handle:
                while chunk := handle.read(_CHUNK):
                    hasher.update(chunk)
            self._hash[key] = hasher.hexdigest()
        return self._hash[key]

    def _record(self, path: Path) -> tuple:
        return self._relative(path), self._sha(path)

    def _read_bytes(self, path: Path) -> bytes:
        """Bind only a completed read, before decoding/parsing can fail.

        The digest is of these bytes, never a second read of a file that may
        have been replaced. The separate streaming _record callback remains
        available to the folder mask scanner.
        """
        data = self._readable(path).read_bytes()
        self._hash[str(path)] = hashlib.sha256(data).hexdigest()
        self._consumed[str(path)] = self._hash[str(path)]
        return data

    def _read_text(self, path: Path) -> str:
        return decode_source_text(self._read_bytes(path), path.name)

    def _remember(self, path: Path, digest):
        """Reuse the digest belonging to cached parsed bytes, if read safely."""
        self._readable(path)
        if digest is not None:
            self._consumed[str(path)] = digest

    def _bindings(self) -> tuple:
        return tuple((self._relative(Path(path)), digest) for path, digest in self._consumed.items())

    def _load(self, path: Path):
        return json.loads(self._read_text(path))  # UTF-8 (BOM allowed), else this machine's code page

    def _coco_index(self, path: Path) -> Optional[dict]:
        """{file name: ('labels', names, width, height) | ('error', message)}, None when the document is not COCO, or
        ('invalid', message) when it cannot be read (kept, so a broken document is parsed once per build)."""
        key = str(path)
        if key in self._coco:
            self._remember(path, self._coco_hash[key])
        if key not in self._coco:
            try:
                self._coco[key] = self._build_coco_index(path)
            except _Refused as exc:
                self._coco[key] = ('invalid', str(exc))
            except MemoryError:
                raise
            except Exception as exc:  # parsed once: every image it may bind gets the same error without a re-parse
                self._coco[key] = ('invalid', f'INVALID_ANNOTATION: {self._relative(path)}: {type(exc).__name__}: {exc}')
            finally:
                self._coco_hash[key] = self._consumed.get(key)
        return self._coco[key]

    def _build_coco_index(self, path: Path) -> Optional[dict]:
        data = self._load(path)
        if not isinstance(data, dict) or not {'images', 'categories', 'annotations'} <= set(data):
            return None
        names = {category['id']: str(category['name']) for category in data['categories']}
        labels, unknown = {}, {}
        for annotation in data['annotations']:
            category = annotation['category_id']
            if category not in names:
                unknown.setdefault(annotation['image_id'], category)
            else:
                labels.setdefault(annotation['image_id'], set()).add(names[category])
        index: dict = {}
        for image in data['images']:
            name = safe_name(image['file_name']).removeprefix('./')
            if name in index:
                index[name] = ('error', 'AMBIGUOUS_ANNOTATION: the COCO document maps this image more than once')
            elif image['id'] in unknown:
                index[name] = ('error', f'INVALID_ANNOTATION: an annotation names unknown category {unknown[image["id"]]!r}')
            else:
                index[name] = ('labels', tuple(sorted(labels.get(image['id'], ()))), image.get('width'), image.get('height'))
        return index

    def _class_list(self, path: Path) -> list:
        key = str(path)
        if key in self._classes or key in self._class_errors:
            self._remember(path, self._class_hash[key])
        if key in self._class_errors:
            raise _Refused(self._class_errors[key])
        if key not in self._classes:
            try:
                self._classes[key] = self._read_class_list(path)
            except MemoryError:
                raise
            except Exception as exc:
                # Cache the message, not a traceback retaining decoded source
                # bytes and the parser's temporary objects.
                self._class_errors[key] = str(exc) if isinstance(exc, _Refused) else (
                    f'INVALID_ANNOTATION: {type(exc).__name__}: {exc}')
                raise
            finally:
                self._class_hash[key] = self._consumed.get(key)
        return self._classes[key]

    def _read_class_list(self, path: Path) -> list:
        text = self._read_text(path)
        if path.name == 'classes.txt':
            classes = text.splitlines()
        else:
            import yaml
            document = yaml.safe_load(text)
            declared = document.get('names') if isinstance(document, dict) else None
            if isinstance(declared, list):
                classes = [str(name) for name in declared]
            elif isinstance(declared, dict):
                indexed = {int(number): str(name) for number, name in declared.items()}
                if set(indexed) != set(range(len(indexed))):
                    raise _Refused(f'INVALID_ANNOTATION: {self._relative(path)} class ids must be contiguous from 0')
                classes = [indexed[number] for number in range(len(indexed))]
            else:
                raise _Refused(f'INVALID_ANNOTATION: {self._relative(path)} has no names list')
        return classes

    def scan(self, image: Path, relative: str, size: Optional[tuple] = None) -> SourceAnnotation:
        """The source annotation of one image; ``size`` (width, height) lets COCO sizes be checked as the importer does."""
        self._consumed = {}
        try:
            return self._scan(Path(image), relative, size)
        except _Refused as exc:
            return SourceAnnotation(None, (), self._bindings(), str(exc))
        except MemoryError:
            raise
        except Exception as exc:  # a user's annotation file must never stop the build
            return SourceAnnotation(None, (), self._bindings(), f'INVALID_ANNOTATION: {type(exc).__name__}: {exc}')
        finally:
            self._consumed = {}

    def _labelme(self, image: Path) -> Optional[SourceAnnotation]:
        adjacent = image.with_suffix('.json')
        if not adjacent.is_file():
            return None
        document = self._load(adjacent)
        if not isinstance(document, dict) or not isinstance(document.get('shapes'), list):
            self._consumed.pop(str(adjacent), None)
            return None
        named = document.get('imagePath')
        if isinstance(named, str) and named:
            if PurePosixPath(named.replace('\\', '/')).name.casefold() != image.name.casefold():
                self._consumed.pop(str(adjacent), None)
                return None  # this document describes another image
        else:
            siblings = [image.with_suffix(suffix) for suffix in SUPPORTED_IMAGE_EXTENSIONS if suffix != image.suffix.lower()]
            if any(path.is_file() and path.name != image.name for path in siblings):
                return SourceAnnotation('labelme', (), (self._record(adjacent),),
                                        f'AMBIGUOUS_ANNOTATION: {self._relative(adjacent)} has no imagePath and more than one image shares its name')
        labels = sorted({str(shape['label']) for shape in document['shapes'] if isinstance(shape, dict) and shape.get('label')})
        return SourceAnnotation('labelme', tuple(labels), (self._record(adjacent),))

    def _scan(self, image: Path, relative: str, size: Optional[tuple]) -> SourceAnnotation:
        found = self._labelme(image)
        if found is not None:
            return found
        files = source_annotation_files(self.source, image)
        hits = []
        for path in files:
            if path.suffix != '.json':
                continue
            index = self._coco_index(path)
            if index is None:
                self._consumed.pop(str(path), None)
                continue
            if isinstance(index, tuple):  # a document that cannot be read may bind this image: say so
                return SourceAnnotation('coco', (), self._bindings(), index[1])
            candidates = source_coco_image_names(self.source, image, path)
            matched = [name for name in candidates if name in index]
            if matched:
                hits.append((path, matched))
            else:
                self._consumed.pop(str(path), None)
        if len(hits) > 1:
            return SourceAnnotation('coco', (), self._bindings(),
                                    'AMBIGUOUS_ANNOTATION: more than one COCO document binds this image')
        if hits:
            path, matched = hits[0]
            recorded = self._bindings()
            if len(matched) > 1:
                return SourceAnnotation('coco', (), recorded, 'AMBIGUOUS_ANNOTATION: the COCO document maps this image more than once')
            try:
                validate_source_coco_binding(self.source, path, matched[0])
            except ValueError as exc:
                return SourceAnnotation('coco', (), recorded, f'AMBIGUOUS_ANNOTATION: {exc}')
            entry = self._coco_index(path)[matched[0]]
            if entry[0] == 'error':
                return SourceAnnotation('coco', (), recorded, entry[1])
            _kind, labels, width, height = entry
            if size is not None and None not in (width, height) and (width, height) != tuple(size):
                return SourceAnnotation('coco', (), recorded,
                                        f'INVALID_ANNOTATION: the COCO size {width}x{height} differs from the image {size[0]}x{size[1]}')
            return SourceAnnotation('coco', labels, recorded)
        label_file = next((path for path in files if path.suffix == '.txt' and path.name != 'classes.txt'), None)
        if label_file is None:
            if self.task not in (None, 'segmentation'):
                return NONE
            mask = self._source_masks.scan(image, size)
            return NONE if mask is None else SourceAnnotation('mask', *mask)
        sources = [path for path in files if path.name == 'classes.txt'] + [path for path in files if path.suffix == '.yaml']
        if not sources:
            self._read_bytes(label_file)
            return SourceAnnotation('yolo', (), self._bindings(),
                                    'INVALID_ANNOTATION: YOLO labels need classes.txt or a data.yaml names list')
        chosen = sources[0]
        classes = self._class_list(chosen)
        disagreeing = [path for path in sources[1:] if self._class_list(path) != classes]
        if disagreeing:
            return SourceAnnotation('yolo', (), self._bindings(),
                                    'AMBIGUOUS_ANNOTATION: the class lists of this image disagree')
        ids = [int(line.split()[0]) for line in self._read_text(label_file).splitlines() if line.strip()]
        recorded = self._bindings()
        if any(not 0 <= value < len(classes) for value in ids):
            return SourceAnnotation('yolo', (), recorded, 'INVALID_ANNOTATION: a YOLO class id is outside the class list')
        if any(not classes[value].strip() for value in ids):
            return SourceAnnotation('yolo', (), recorded, 'INVALID_ANNOTATION: a YOLO class id names a blank class')
        return SourceAnnotation('yolo', tuple(sorted({classes[value] for value in ids})),
                                (self._record(label_file), self._record(chosen)))
