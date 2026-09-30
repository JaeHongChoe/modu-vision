"""Learned upright rotation: real CPU optimization and native geometry contracts."""
import hashlib
import json
import threading

import numpy as np
import pytest
import torch
from PIL import Image, ImageDraw


def rotation_data(root):
    root.mkdir()
    rows = []
    for split, copies in [('train', 2), ('val', 1), ('test', 1)]:
        for angle in (0, 90, 180, -90):
            for copy in range(copies):
                image = Image.new('RGB', (32, 32), 'black')
                ImageDraw.Draw(image).polygon([(16, 2), (5, 25), (16, 19), (24, 25)], fill='white')
                image = image.rotate(-angle)
                image.putpixel((0, 0), (len(rows) + 1, 0, 0))
                path = root / f'{split}_{angle}_{copy}.png'
                image.save(path)
                rows.append(dict(image=path.name, correction_deg=angle, split=split))
    return rows


def test_learned_rotation_cpu_training_and_offline_native_inference(tmp_path):
    from backend.engine.rotation import write_rotation_manifest, train_rotation, evaluate_rotation_checkpoint, predict_rotation_array, export_rotation_package, run_rotation_package
    root = tmp_path / 'images'
    rows = rotation_data(root)
    write_rotation_manifest(root, rows)
    output = tmp_path / 'candidate'
    torch.set_num_threads(1)
    result = train_rotation(root, output, epochs=20, image_size=32, batch_size=8, learning_rate=.01, seed=3)
    assert result['training_loss_history'][-1] < result['training_loss_history'][0]
    evaluated = evaluate_rotation_checkpoint(output / 'best_model.pt', root, split='test')
    assert evaluated['sample_count'] == 4
    assert evaluated['angular_mae_deg'] < 45
    native = np.asarray(Image.open(root / 'test_90_0.png').resize((83, 51)))
    prediction = predict_rotation_array(output / 'best_model.pt', native)
    assert prediction['aligned_image'].dtype == np.uint8
    assert prediction['source_size'] == [83, 51]
    assert prediction['transform'].shape == (3, 3)
    assert abs(prediction['correction_deg'] - 90) < 45
    package = export_rotation_package(output / 'best_model.pt', tmp_path / 'package')
    deployed = run_rotation_package(package, native)
    assert abs(deployed['correction_deg'] - prediction['correction_deg']) < 1e-4
    np.testing.assert_array_equal(deployed['aligned_image'], prediction['aligned_image'])


def test_rotation_truth_gate_source_identity_and_cancellation(tmp_path):
    from backend.engine.rotation import write_rotation_manifest, train_rotation, load_rotation_manifest
    root = tmp_path / 'images'
    rows = rotation_data(root)
    with pytest.raises(ValueError, match='finite|angle'):
        write_rotation_manifest(root, [{**rows[0], 'correction_deg': float('nan')}])
    write_rotation_manifest(root, rows)
    manifest = load_rotation_manifest(root)
    assert manifest.provenance['split_counts'] == {'train': 8, 'val': 4, 'test': 4}
    event = threading.Event(); event.set()
    with pytest.raises(InterruptedError, match='cancel'):
        train_rotation(root, tmp_path / 'cancelled', epochs=2, cancel_event=event)
    assert not (tmp_path / 'cancelled' / 'best_model.pt').exists()
    source = root / rows[0]['image']; source.write_bytes((root / rows[2]['image']).read_bytes())
    with pytest.raises(ValueError, match='hash|SHA|changed'):
        load_rotation_manifest(root)


def test_rotation_checkpoint_source_and_export_tampering_rejected(tmp_path):
    from backend.engine.rotation import write_rotation_manifest, train_rotation, evaluate_rotation_checkpoint, export_rotation_package, run_rotation_package
    root = tmp_path / 'images'; write_rotation_manifest(root, rotation_data(root))
    output = tmp_path / 'candidate'; train_rotation(root, output, epochs=1, image_size=32)
    package = export_rotation_package(output / 'best_model.pt', tmp_path / 'package')
    (package / 'model.ts').write_bytes(b'invalid')
    with pytest.raises(ValueError, match='checksum|hash'):
        run_rotation_package(package, np.zeros((32, 32, 3), dtype=np.uint8))
    rows = json.loads((root / 'rotation.json').read_text())
    rows['samples'][0]['correction_deg'] += 3
    (root / 'rotation.json').write_text(json.dumps(rows))
    with pytest.raises(ValueError, match='provenance|dataset'):
        evaluate_rotation_checkpoint(output / 'best_model.pt', root, split='test')
