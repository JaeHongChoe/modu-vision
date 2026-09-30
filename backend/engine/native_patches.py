"""Native pixel grids and bounded crop batches shared by app and standalone inference."""
from itertools import islice

DEFAULT_MAX_IMAGE_PIXELS = 100_000_000
DEFAULT_MAX_PATCH_COUNT = 1_000_000
DEFAULT_MAX_BATCH_BYTES = 256 * 1024 * 1024


def validate_image_size(width, height, max_image_pixels=DEFAULT_MAX_IMAGE_PIXELS):
    if any(type(value) is not int or value < 1 for value in (width, height, max_image_pixels)):
        raise ValueError('Patch image dimensions and pixel guard must be positive integers')
    if width * height > max_image_pixels:
        raise ValueError(f'Patch image has {width * height} pixels; safety limit is {max_image_pixels}')


def validate_geometry(patch_size, stride):
    if type(patch_size) is not int or patch_size < 1 or type(stride) is not int or not 1 <= stride <= patch_size:
        raise ValueError('Patch size and stride must be positive integers with stride <= patch_size')


def grid_positions(length, patch_size, stride):
    if length <= patch_size:
        yield 0
        return
    last = length - patch_size
    yield from range(0, last + 1, stride)
    if last % stride:
        yield last


def validate_patch_count(count, max_patches=None, max_patch_count=DEFAULT_MAX_PATCH_COUNT):
    if type(max_patch_count) is not int or max_patch_count < 1:
        raise ValueError('Patch count safety limit must be a positive integer')
    if max_patches is not None and (type(max_patches) is not int or max_patches < 1):
        raise ValueError('max_patches must be None or a positive integer')
    if count > max_patch_count:
        raise ValueError(f'Patch inspection needs {count} crops; safety limit is {max_patch_count}')
    if max_patches is not None and count > max_patches:
        raise ValueError(f'Patch inspection needs {count} crops; limit is {max_patches}')


def native_grid(width, height, patch_size, stride, *, max_patches=None,
                max_image_pixels=DEFAULT_MAX_IMAGE_PIXELS, max_patch_count=DEFAULT_MAX_PATCH_COUNT):
    """Validate total work before yielding source-coordinate boxes in row-major order."""
    validate_image_size(width, height, max_image_pixels)
    validate_geometry(patch_size, stride)
    def count(length):
        return 1 if length <= patch_size else (length - patch_size) // stride + 1 + bool((length - patch_size) % stride)
    validate_patch_count(count(width) * count(height), max_patches, max_patch_count)
    return ((x, y, min(x + patch_size, width), min(y + patch_size, height))
            for y in grid_positions(height, patch_size, stride)
            for x in grid_positions(width, patch_size, stride))


def bounded_batch_size(image_size, requested=32, max_batch_bytes=DEFAULT_MAX_BATCH_BYTES):
    if (not isinstance(image_size, (list, tuple)) or len(image_size) != 2
            or any(type(value) is not int or value < 1 for value in image_size)
            or type(requested) is not int or requested < 1
            or type(max_batch_bytes) is not int or max_batch_bytes < 1):
        raise ValueError('Patch input dimensions, batch size and memory guard must be positive integers')
    # Account for float crop conversion, normalization, stacking and device input.
    bytes_per_patch = image_size[0] * image_size[1] * 3 * 4 * 4
    capacity = max_batch_bytes // bytes_per_patch
    if capacity < 1:
        raise ValueError('One patch model input exceeds the batch memory safety limit')
    return min(requested, capacity)


def iter_patch_batches(boxes, batch_size):
    iterator = iter(boxes)
    while True:
        batch = list(islice(iterator, batch_size))
        if not batch:
            return
        yield batch
