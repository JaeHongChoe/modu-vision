"""Checksum-verified portable generation workflow; output requires human review."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from backend.engine.defect_gan import generate_defect_candidates


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_generator_package(checkpoint: Path, output_dir: Path) -> Path:
    from backend.engine.specialized_models import require_completed_checkpoint
    require_completed_checkpoint(checkpoint)
    checkpoint=Path(checkpoint);output=Path(output_dir)
    if checkpoint.is_symlink() or not checkpoint.is_file() or checkpoint.name!='best_model.pt': raise ValueError('A regular GAN checkpoint is required')
    metadata=checkpoint.with_name('model_meta.json')
    if metadata.is_symlink() or not metadata.is_file(): raise ValueError('GAN checkpoint metadata is required')
    meta=json.loads(metadata.read_text())
    if meta.get('checkpoint_sha256')!=_sha(checkpoint): raise ValueError('GAN checkpoint checksum differs from metadata')
    if output.exists() or output.is_symlink():raise ValueError('Generation package needs a new output directory')
    output.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.gan-package-',dir=output.parent))
    try:
        shutil.copyfile(checkpoint,staging/'best_model.pt');shutil.copyfile(metadata,staging/'model_meta.json')
        engine=Path(__file__).parent
        for name in ('defect_gan.py','gan_package_runtime.py','runtime_device.py'):
            target=staging/'backend'/'engine'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(engine/name,target)
        (staging/'backend'/'__init__.py').write_text('')
        (staging/'backend'/'engine'/'__init__.py').write_text('')
        (staging/'generate.py').write_text('from backend.engine.gan_package_runtime import main\nif __name__ == "__main__": raise SystemExit(main())\n')
        (staging/'requirements.txt').write_text('numpy>=1.26\nPillow>=10.4\ntorch>=2.4\n')
        (staging/'workflow.json').write_text(json.dumps({'task':'defect_gan','stages':['explicit_defect_crops','trained_generator','heldout_diagnostic','generate_unreviewed','human_review','adopt_train_only'],'output_state':'synthetic_unreviewed','quality_status':'unvalidated'}))
        (staging/'README.md').write_text('# GAN generation workflow\nInstall requirements, then run `python generate.py --output /new/review/directory --count 8 --seed 0 --device cpu`. The manifest verifies every package file before checkpoint loading. Generated candidates require review and an explicit defect label before adoption into training data. This workflow does not issue an inspection verdict.\n')
        files=[{'path':p.relative_to(staging).as_posix(),'sha256':_sha(p)} for p in sorted(staging.rglob('*')) if p.is_file()]
        (staging/'manifest.json').write_text(json.dumps({'schema_version':1,'task':'defect_gan','generator_sha256':_sha(checkpoint),'files':files},indent=2))
        staging.rename(output)
        return output.resolve()
    except BaseException:shutil.rmtree(staging,ignore_errors=True);raise


def run_generator_package(package_dir,output_dir,*,count=8,seed=0,device='cpu'):
    from backend.engine.runtime_device import resolve_runtime_device
    root=Path(package_dir).resolve();manifest=json.loads((root/'manifest.json').read_text())
    if manifest.get('schema_version')!=1 or manifest.get('task')!='defect_gan':raise ValueError('Invalid GAN generation package')
    seen=set()
    for row in manifest['files']:
        relative=Path(row['path']);path=root/relative
        if relative.is_absolute() or '..' in relative.parts or row['path'] in seen or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root) or _sha(path)!=row['sha256']:
            raise ValueError('GAN package checksum or path mismatch')
        seen.add(row['path'])
    if not {'best_model.pt','model_meta.json','generate.py','workflow.json'}.issubset(seen):raise ValueError('Incomplete GAN generation package')
    output=Path(output_dir)
    if output.exists() and any(output.iterdir()):raise ValueError('Use a new empty generation review directory')
    return generate_defect_candidates(root/'best_model.pt',output,count=count,seed=seed,device=str(resolve_runtime_device(device)))


def main():
    parser=argparse.ArgumentParser(description='Generate unreviewed defect candidates from a verified package')
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--count',type=int,default=8);parser.add_argument('--seed',type=int,default=0);parser.add_argument('--device',default='cpu')
    args=parser.parse_args()
    try:
        result=run_generator_package(Path(__file__).resolve().parents[2],args.output,count=args.count,seed=args.seed,device=args.device)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (ValueError,OSError) as exc:parser.exit(2,f'Generation package error: {exc}\n')
