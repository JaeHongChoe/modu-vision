"""Read-only source bindings for the folder segmentation loader's raster masks.

Only native-size 8-bit grayscale PNG masks are accepted. Palette/color masks need
the explicit mask-manifest import workflow; converting their channels would lose
class identity. Value 0 is background, and binary 0/255 follows the folder loader's
255 -> 1 mapping. A background-only mask supplies no normal/OK truth.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile

from PIL import Image

from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS, _class_map_names, mask_folder_layout, sanitize_file_stem


class SourceMaskScanner:
    def __init__(self, source: Path, *, readable, record):
        self.source = source
        self.readable = readable
        self.record = record
        self._masks = {}  # decoded geometry, distinct values and digest; never retained pixels
        self._classes = {}

    @staticmethod
    def _pair(image: Path, masks: Path):
        clean, raw = sanitize_file_stem(image), image.stem
        for stem in dict.fromkeys((clean, raw)):
            for name in (f'{stem}.png', f'{stem}_mask.png', f'{stem}.mask.png'):
                path = masks / name
                if path.exists() or path.is_symlink():
                    return path
        return None

    def _layout(self, image: Path):
        for folder in image.parents:
            if not folder.is_relative_to(self.source):
                break
            if folder.name != 'images':
                continue
            local = image.relative_to(folder).parts
            root = folder.parent
            if len(local) == 1:
                _train_images, train_masks, _val_images, _val_masks = mask_folder_layout(root)
                return root, train_masks
            if len(local) == 2 and local[0] in ('train', 'val', 'test'):
                split = local[0]
                if split == 'test':
                    return root, root / 'masks' / 'test'
                _train_images, train_masks, _val_images, val_masks = mask_folder_layout(root)
                return root, train_masks if split == 'train' else val_masks
            return None
        return None

    def _mask(self, path: Path):
        if path not in self._masks:
            digest, size, values, error = None, None, (), None
            relative = path.relative_to(self.source).as_posix()
            try:
                # Decode and hash the same bytes, without holding a high-resolution
                # compressed file in memory or changing the source file.
                with self.readable(path).open('rb') as source, tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as data:
                    hasher = hashlib.sha256()
                    while chunk := source.read(1024 * 1024):
                        hasher.update(chunk)
                        data.write(chunk)
                    digest = hasher.hexdigest()
                    data.seek(0)
                    with Image.open(data) as image:
                        if image.format != 'PNG' or image.mode != 'L':
                            raise ValueError('masks require single-channel 8-bit grayscale (L) PNG class IDs')
                        image.load()
                        size = image.size
                        values = tuple(value for value, count in enumerate(image.histogram()) if count)
            except MemoryError:
                raise
            except Exception as exc:
                message = str(exc) if str(exc).startswith('INVALID_ANNOTATION:') else (
                    f'INVALID_ANNOTATION: {relative}: {str(exc) if isinstance(exc, ValueError) else type(exc).__name__}')
                error = message
            self._masks[path] = digest, size, values, error
        return self._masks[path]

    def _class_names(self, root: Path, current_values):
        if root not in self._classes:
            mapping = root / 'class_map.json'
            names = _class_map_names(self.readable(mapping)) if mapping.is_file() or mapping.is_symlink() else {}
            largest = max(1, max(names, default=0))
            train_images, train_masks, val_images, val_masks = mask_folder_layout(root)
            # The unnamed class list depends on every paired train/val mask,
            # as in mask_folder_classes; one binary image must not invent a
            # different name for class 1 in a multiclass dataset.
            for images, masks in dict.fromkeys(((train_images, train_masks), (val_images, val_masks))):
                for image in images.glob('*'):
                    if not image.is_file() or image.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
                        continue
                    mask = self._pair(image, masks)
                    if mask is not None:
                        _digest, _size, values, error = self._mask(mask)
                        if error is None and not set(values).issubset({0, 255}):
                            largest = max(largest, max(values, default=0))
            classes = ['background' if value == 0 else names.get(value) or ('defect' if largest == 1 else f'class_{value}')
                       for value in range(largest + 1)]
            repeated = sorted({name for name in classes if classes.count(name) > 1})
            if repeated:
                raise ValueError(f'class_map.json names a class more than once (value 0 is always background): {", ".join(repeated)}')
            self._classes[root] = names, largest
        names, largest = self._classes[root]
        values = (1,) if 255 in current_values and set(current_values).issubset({0, 255}) else current_values
        return tuple(sorted({names.get(value) or ('defect' if largest == 1 and value == 1 else f'class_{value}')
                             for value in values if value != 0}))

    def scan(self, image: Path, size):
        """None if no folder mask binds; otherwise (foreground labels, binding files, error)."""
        layout = self._layout(image)
        if layout is None:
            return None
        root, masks = layout
        mask = self._pair(image, masks)
        if mask is None:
            return None
        digest, geometry, values, error = self._mask(mask)
        files = ((mask.relative_to(self.source).as_posix(), digest),) if digest is not None else ()
        if error is not None:
            return (), files, error
        mapping = root / 'class_map.json'
        try:
            if mapping.is_file() or mapping.is_symlink():
                files += (self.record(mapping),)
            if size is not None and geometry != tuple(size):
                return (), files, f'INVALID_ANNOTATION: the mask size {geometry[0]}x{geometry[1]} differs from the image {size[0]}x{size[1]}'
            return self._class_names(root, values), files, None
        except MemoryError:
            raise
        except Exception as exc:
            message = str(exc)
            if not message.startswith('INVALID_ANNOTATION:'):
                message = f'INVALID_ANNOTATION: class_map.json: {message}'
            return (), files, message
