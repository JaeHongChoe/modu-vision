"""Actual optional Pillow codecs, authored non-patient pixels, no quality approval."""
import hashlib
import io

import numpy as np
import pytest
from PIL import Image, features

pydicom = pytest.importorskip("pydicom")
from pydicom.dataset import Dataset, FileDataset
from pydicom.encaps import encapsulate
from pydicom.uid import JPEGBaseline8Bit, JPEG2000Lossless, generate_uid
from backend.engine.dicom_input import read_dicom, normalized_view, read_cached_view


@pytest.mark.parametrize("codec,mode,selected", [
    ("jpeg", "mono", 0), ("jpeg", "rgb", 0),
    ("jpeg", "multi", 0), ("jpeg", "multi", 1),
    ("jpeg2000", "mono16", 0), ("jpeg2000", "rgb", 0),
    ("jpeg2000", "multi", 0), ("jpeg2000", "multi", 1),
])
def test_actual_compressed_source_frame_display_and_reopen(tmp_path, codec, mode, selected):
    pillow_codec = "jpg" if codec == "jpeg" else "jpg_2000"
    if not features.check_codec(pillow_codec):
        pytest.skip(f"Actual optional Pillow {pillow_codec} encoder/decoder is unavailable")
    y, x = np.indices((16, 32))
    pixels = (x * 7 + y * 2).astype(np.uint8)
    if mode == "mono16":
        pixels = pixels.astype(np.uint16) * 13
    elif mode == "rgb":
        pixels = np.stack([pixels, pixels // 2, 255 - pixels], axis=-1)
    originals = [pixels, np.flip(pixels, axis=1).copy()] if mode == "multi" else [pixels]
    encoded, reference = [], []
    for frame in originals:
        stream = io.BytesIO()
        Image.fromarray(frame).save(stream, format="JPEG" if codec == "jpeg" else "JPEG2000",
            **({"quality": 95, "subsampling": 0} if codec == "jpeg" else {"irreversible": False}))
        compressed = stream.getvalue()
        encoded.append(compressed)
        # Independent Pillow input is the same encoded frame, not a DICOM
        # decoder result reused as its own expected answer. JPEG is lossy.
        with Image.open(io.BytesIO(compressed)) as image:
            reference.append(np.array(image.convert("RGB") if mode == "rgb" else image))
    uid = JPEGBaseline8Bit if codec == "jpeg" else JPEG2000Lossless
    meta = Dataset()
    meta.TransferSyntaxUID = uid
    meta.MediaStorageSOPClassUID = generate_uid()
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.ImplementationClassUID = generate_uid()
    source = tmp_path / "authored-compressed.dcm"
    ds = FileDataset(str(source), {}, file_meta=meta, preamble=bytes(128))
    ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
    ds.Rows, ds.Columns = 16, 32
    ds.SamplesPerPixel = 3 if mode == "rgb" else 1
    ds.PhotometricInterpretation = ("YBR_FULL_422" if codec == "jpeg" and mode == "rgb"
        else "RGB" if mode == "rgb" else "MONOCHROME2")
    ds.BitsAllocated = 16 if mode == "mono16" else 8
    ds.BitsStored, ds.HighBit, ds.PixelRepresentation = ds.BitsAllocated, ds.BitsAllocated - 1, 0
    if mode == "rgb":
        ds.PlanarConfiguration = 0
    if mode == "multi":
        ds.NumberOfFrames = 2
    ds.PixelData = encapsulate(encoded)
    ds["PixelData"].is_undefined_length = True
    ds.save_as(source, enforce_file_format=True)
    original_bytes = source.read_bytes()
    options = {"frame_index": selected} if mode == "multi" else {}
    if mode == "multi":
        with pytest.raises(ValueError, match="explicit frame_index"):
            read_dicom(source)
        with pytest.raises(ValueError, match="outside available frames"):
            read_dicom(source, frame_index=2)
    # Lossless JPEG2000 must also preserve the authored, pre-encoding pixels.
    if codec == "jpeg2000":
        assert np.array_equal(reference[selected], originals[selected])
    expected = reference[selected]
    if mode != "rgb":
        values = expected.astype(float)
        low, high = float(values.min()), float(values.max())
        gray = np.rint((values - low) / max(1., high - low) * 255).astype(np.uint8)
        expected = np.repeat(gray[:, :, None], 3, axis=2)
    display, metadata = read_dicom(source, **options)
    assert np.array_equal(np.array(display), expected)
    assert metadata["transfer_syntax_uid"] == str(uid)
    assert metadata["source_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert metadata["frame_index"] == selected
    assert metadata["coordinate_space"] == "native_source_pixels"
    views = tmp_path / "project-owned-views"
    receipt = normalized_view(source, views, **options)
    cached = read_cached_view(receipt["view_id"], views, resolve_source=lambda _: source)
    with Image.open(io.BytesIO(cached)) as reopened:
        assert np.array_equal(np.array(reopened), expected)
    assert normalized_view(source, views, **options) == receipt
    assert source.read_bytes() == original_bytes
