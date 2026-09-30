from __future__ import annotations

from pathlib import Path
import json

import numpy as np
from PIL import Image
import pytest

from backend.engine.defect_gan import (
    generate_defect_candidates,
    load_defect_gan_manifest,
    train_defect_gan,
    write_defect_gan_manifest,
)


def _source(tmp_path: Path) -> tuple[Path, list[dict]]:
    root = tmp_path / "source"
    root.mkdir(parents=True)
    rows = []
    for index in range(4):
        pixels = np.zeros((64, 64, 3), dtype=np.uint8)
        pixels[:, :, 0] = 40 + index * 35
        pixels[10 + index:40 + index, 12:44, 1] = 200
        image = root / f"source_{index}.png"
        Image.fromarray(pixels).save(image)
        rows.append({"image": image.name, "bbox": [8, 8, 48, 48], "split": "train"})
    return root, rows


def test_training_cancel_reaches_real_batch_boundary_before_export(tmp_path):
    import threading
    root,rows=_source(tmp_path);write_defect_gan_manifest(root,rows)
    event=threading.Event();progress=[]
    def cancel_after_batch(values):progress.append(values);event.set()
    with pytest.raises(InterruptedError,match='cancelled'):
        train_defect_gan(root,tmp_path/'cancelled',epochs=2,batch_size=2,base_channels=8,cancel_event=event,on_progress=cancel_after_batch)
    assert progress[0]['batch']==1 and progress[0]['epoch']==1
    assert not (tmp_path/'cancelled/best_model.pt').exists()


def test_defect_gan_saves_a_trained_generator_and_review_only_candidates(tmp_path: Path):
    root, rows = _source(tmp_path)
    write_defect_gan_manifest(root, rows)
    manifest = load_defect_gan_manifest(root)
    assert len(manifest["samples"]) == 4

    result = train_defect_gan(root, tmp_path / "model", epochs=1, batch_size=2, seed=7,
                              device="cpu", base_channels=8)
    assert result["status"] == "completed"
    checkpoint = Path(result["checkpoint_path"])
    assert checkpoint.is_file()
    assert json.loads((checkpoint.parent / "model_meta.json").read_text())["model_kind"] == "dcgan_defect_crop"

    generated = generate_defect_candidates(checkpoint, tmp_path / "review", count=2, seed=19,
                                           device="cpu")
    assert len(generated["candidates"]) == 2
    assert all(item["status"] == "synthetic_unreviewed" for item in generated["candidates"])
    assert all(Path(item["path"]).is_file() for item in generated["candidates"])
    assert all(Image.open(item["path"]).size == (64, 64) for item in generated["candidates"])
    assert generated["generator_sha256"]
    assert generated["source_manifest_sha256"]
    checkpoint.write_bytes(checkpoint.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="hash changed"):
        generate_defect_candidates(checkpoint, tmp_path / "review_2", count=1)


def test_defect_gan_rejects_changed_source_and_leaking_split(tmp_path: Path):
    root, rows = _source(tmp_path)
    write_defect_gan_manifest(root, rows)
    Image.new("RGB", (64, 64), "white").save(root / rows[0]["image"])
    with pytest.raises(ValueError, match="changed|hash"):
        load_defect_gan_manifest(root)

    root2, rows2 = _source(tmp_path / "other")
    rows2.append({**rows2[0], "split": "val"})
    with pytest.raises(ValueError, match="split|duplicate"):
        write_defect_gan_manifest(root2, rows2)
