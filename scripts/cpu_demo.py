"""CPU demo for a fresh checkout: a real training run with no GPU, network or downloaded weights.

    python scripts/cpu_demo.py [--workdir PATH] [--keep]

It draws a small synthetic dataset (an asymmetric mark at known angles, split into train/val/test), then runs the
same training engine the app uses through its CLI: prepare the rotation task, train the small rotation model for 20
epochs on the CPU, and evaluate it on the held-out test images. The result is the measured angular error and the
delivered model files. It shows that the install works end to end; it says nothing about model quality on real parts.
The job record and stores the training engine writes stay in the demo's own folder; the user's app data is not touched.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPLITS = {'train': 120, 'val': 16, 'test': 16}
EPOCHS = 20
DELIVERED = {'model.pt', 'model_meta.json', 'configuration.json', 'evaluation.json', 'artifacts.json'}


def draw(path: Path, angle: float, dx: int, dy: int) -> None:
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (64, 64), 'black')
    pen = ImageDraw.Draw(image)
    # (dx, dy): a little placement jitter, as on a real fixture
    pen.rectangle((28 + dx, 10 + dy, 36 + dx, 54 + dy), fill='white')   # stem
    pen.rectangle((36 + dx, 10 + dy, 52 + dx, 18 + dy), fill='white')   # flag at the top right: up/down and left/right
    pen.ellipse((12 + dx, 44 + dy, 20 + dx, 52 + dy), fill=(255, 64, 64))
    image.rotate(angle, fillcolor='black').save(path)


def dataset(source: Path) -> Path:
    """Images rotated counterclockwise by a known angle; the truth is the counterclockwise correction back upright."""
    rng = random.Random(7)
    # Every image is a distinct (angle, placement) pair drawn without replacement, so no image repeats within or across
    # splits (the engine refuses repeated images across splits).
    poses = rng.sample([(angle, dx, dy) for angle in range(-165, 181, 15) for dx in range(-3, 4) for dy in range(-3, 4)],
                       sum(SPLITS.values()))
    samples = []
    for split, count in SPLITS.items():
        for index in range(count):
            angle, dx, dy = poses.pop()
            name = f'{split}_{index:02d}.png'
            draw(source / name, angle, dx, dy)
            samples.append({'image': name, 'split': split, 'correction_deg': -angle})
    labels = source.parent / 'labels.json'
    labels.write_text(json.dumps({'samples': samples}, indent=1), encoding='utf-8')
    return labels


def cli(app_data: Path, *arguments: str) -> list[dict]:
    """Run the training CLI; its stdout is JSON lines. A failure stops the demo with the CLI's own message.

    The demo keeps the app data the CLI writes (the job ledger and stores) in its own folder, never the user's.
    """
    environment = dict(os.environ, PYTHONIOENCODING='utf-8', VISION_AI_STUDIO_USER_DATA_DIR=str(app_data),
                       MODU_SPLIT_MANIFEST_DIR=str(app_data / 'splits'), MODU_THUMBNAIL_CACHE_DIR=str(app_data / 'thumbnails'))
    environment.pop('VISION_RESOURCE_LEASE_DB', None)  # the reservation database then lives in the demo's app data too
    completed = subprocess.run([sys.executable, '-m', 'backend.training_cli', *arguments], cwd=ROOT, env=environment,
                               capture_output=True, text=True, encoding='utf-8', errors='replace')
    if completed.returncode != 0:
        raise SystemExit(f"training CLI failed ({' '.join(arguments[:1])}):\n{completed.stderr[-2000:] or completed.stdout[-2000:]}")
    return [json.loads(line) for line in completed.stdout.splitlines() if line.startswith('{')]


def run(workdir: Path) -> dict:
    source, output, app_data = workdir / 'source images', workdir / 'model output', workdir / 'app data'
    source.mkdir(parents=True)
    labels = dataset(source)
    prepared = cli(app_data, 'prepare', '--task', 'rotation', '--source', str(source), '--labels', str(labels), '--output', str(output))
    trained = cli(app_data, 'train', '--output', str(output), '--mode', 'quick', '--epochs', str(EPOCHS), '--device', 'cpu',
                  '--config-json', json.dumps({'width': 16, 'image_size': 64, 'batch_size': 8, 'learning_rate': 0.003, 'seed': 7}))
    run_id = next(event['run_id'] for event in reversed(trained) if event.get('run_id'))
    evaluated = cli(app_data, 'evaluate', '--output', str(output), '--run-id', run_id, '--split', 'test')
    evaluation = (evaluated[-1] if evaluated else {}).get('evaluation', {})
    delivered = sorted({path.name for path in output.rglob('*') if path.name in DELIVERED})
    return {'prepared_id': (prepared[-1] if prepared else {}).get('prepared_id'), 'run_id': run_id,
            'evaluated_split': evaluation.get('split'), 'test_images': evaluation.get('sample_count'),
            'angular_mae_deg': evaluation.get('angular_mae_deg'),
            'within_10_deg': evaluation.get('within_10_deg'), 'checkpoint_sha256': evaluation.get('checkpoint_sha256'),
            'output': str(output), 'delivered_files': delivered}


def main(argv: list[str] | None = None) -> int:
    options = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    options.add_argument('--workdir', help='Folder for the synthetic data and the model output (default: a temporary folder)')
    options.add_argument('--keep', action='store_true', help='Keep the temporary folder and print its location')
    arguments = options.parse_args(argv)
    if arguments.workdir:
        workdir = Path(arguments.workdir).resolve()
        if workdir.exists() and any(workdir.iterdir()):
            raise SystemExit(f'{workdir} is not empty; choose a new folder')
        result = run(workdir)
    elif arguments.keep:
        result = run(Path(tempfile.mkdtemp(prefix='modu-vision-cpu-demo-')))
    else:
        # (a cleanup error, such as a file still held open on Windows, does not turn a finished demo into a failure)
        with tempfile.TemporaryDirectory(prefix='modu-vision-cpu-demo-', ignore_cleanup_errors=True) as temporary:
            result = run(Path(temporary))
        result['output'] = None  # removed with the temporary folder; use --keep or --workdir to keep it
    # A console whose code page cannot show a path's characters (cp1252 and Korean, say) shows a replacement instead
    # of failing after the training succeeded.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    complete = result['evaluated_split'] == 'test' and result['test_images'] and result['angular_mae_deg'] is not None
    print(json.dumps({'cpu_demo': 'completed' if complete else 'incomplete', **result}, ensure_ascii=False, indent=1))
    return 0 if complete else 1


if __name__ == '__main__':
    raise SystemExit(main())
