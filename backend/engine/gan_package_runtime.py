"""Checksum-verified portable generation workflow; output requires human review."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from backend.engine.defect_gan import generate_defect_candidates, generate_composited_candidates


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# The engine files a generation package carries; runtime_deadline (the owned generation run) needs process_isolation.
GENERATOR_PACKAGE_ENGINE_FILES = ('defect_gan.py', 'gan_package_runtime.py', 'runtime_device.py', 'geometry_measurement.py',
                                  'runtime_deadline.py', 'process_isolation.py', 'runtime_configuration.py', 'native_runtime_bridge.py')


def build_generator_package(checkpoint: Path, output_dir: Path) -> Path:
    from backend.engine.specialized_models import require_completed_checkpoint
    require_completed_checkpoint(checkpoint)
    checkpoint=Path(checkpoint);output=Path(output_dir)
    if checkpoint.is_symlink() or not checkpoint.is_file() or checkpoint.name!='best_model.pt': raise ValueError('A regular GAN checkpoint is required')
    metadata=checkpoint.with_name('model_meta.json')
    if metadata.is_symlink() or not metadata.is_file(): raise ValueError('GAN checkpoint metadata is required')
    meta=json.loads(metadata.read_text(encoding='utf-8'))
    if meta.get('checkpoint_sha256')!=_sha(checkpoint): raise ValueError('GAN checkpoint checksum differs from metadata')
    if output.exists() or output.is_symlink():raise ValueError('Generation package needs a new output directory')
    output.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.gan-package-',dir=output.parent))
    try:
        from backend.engine.native_sdk import copy_native_sdk
        copy_native_sdk(Path(__file__).parent.parents[1]/'native_runtime', staging/'native_runtime')
        shutil.copyfile(checkpoint,staging/'best_model.pt');shutil.copyfile(metadata,staging/'model_meta.json')
        engine=Path(__file__).parent
        for name in GENERATOR_PACKAGE_ENGINE_FILES:
            target=staging/'backend'/'engine'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(engine/name,target)
        (staging/'backend'/'__init__.py').write_text('',encoding='utf-8')
        (staging/'backend'/'engine'/'__init__.py').write_text('',encoding='utf-8')
        (staging/'generate.py').write_text('from backend.engine.gan_package_runtime import main\nif __name__ == "__main__": raise SystemExit(main())\n',encoding='utf-8')
        (staging/'requirements.txt').write_text('numpy>=1.26\nPillow>=10.4\nopencv-python-headless>=4.10\ntorch>=2.4\n',encoding='utf-8')
        (staging/'workflow.json').write_text(json.dumps({'task':'defect_gan','stages':['explicit_defect_crops','trained_generator','heldout_diagnostic','generate_unreviewed','human_review','adopt_train_only'],'output_state':'synthetic_unreviewed','quality_status':'unvalidated'}),encoding='utf-8')
        (staging/'README.md').write_text('# GAN generation workflow\nInstall requirements, then run `python generate.py --output /new/review/directory --count 8 --seed 0 --device cpu`. Add `--source-image /original/image.png --regions /regions.json` to compose into explicit original-coordinate regions. Each region declares `id`, `bbox` and optional `opacity`, `feather_px`, `mask_polygon`. The manifest verifies every package file before checkpoint loading. Generated candidates require review and an explicit defect label before adoption into training data. This workflow does not issue an inspection verdict.\n', encoding='utf-8')
        files=[{'path':p.relative_to(staging).as_posix(),'sha256':_sha(p)} for p in sorted(staging.rglob('*')) if p.is_file()]
        (staging/'manifest.json').write_text(json.dumps({'schema_version':1,'task':'defect_gan','generator_sha256':_sha(checkpoint),'files':files},indent=2),encoding='utf-8')
        staging.rename(output)
        return output.resolve()
    except BaseException:shutil.rmtree(staging,ignore_errors=True);raise


def verify_generator_package(package_dir):
    root=Path(package_dir).resolve();manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version')!=1 or manifest.get('task')!='defect_gan':raise ValueError('Invalid GAN generation package')
    seen=set()
    for row in manifest['files']:
        relative=Path(row['path']);path=root/relative
        if relative.is_absolute() or '..' in relative.parts or row['path'] in seen or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root) or _sha(path)!=row['sha256']:
            raise ValueError('GAN package checksum or path mismatch')
        seen.add(row['path'])
    if not {'best_model.pt','model_meta.json','generate.py','workflow.json'}.issubset(seen):raise ValueError('Incomplete GAN generation package')
    if any(p.relative_to(root).as_posix() not in seen for p in (root/'backend').rglob('*.py')):raise ValueError('GAN package contains unlisted runtime code')
    return root


def run_generator_package(package_dir,output_dir,*,count=8,seed=0,device='cpu',source_image_path=None,regions=None,source_sha256=None):
    from backend.engine.runtime_device import resolve_runtime_device
    root=verify_generator_package(package_dir)
    output=Path(output_dir)
    if output.exists() and any(output.iterdir()):raise ValueError('Use a new empty generation review directory')
    if source_image_path is not None:
        return generate_composited_candidates(root/'best_model.pt',source_image_path,output,
            regions=regions,count=count,seed=seed,device=str(resolve_runtime_device(device)),source_sha256=source_sha256)
    if regions is not None or source_sha256 is not None: raise ValueError('GAN composition needs the original source image')
    return generate_defect_candidates(root/'best_model.pt',output,count=count,seed=seed,device=str(resolve_runtime_device(device)))


class GeneratorExecutor:
    """Native/Python generation retains seeds, source provenance and required review."""
    def __init__(self,package_dir,*,device='cpu',deadline_ms=300000,cpu_threads=1):
        from backend.engine.runtime_configuration import runtime_options
        self.root=verify_generator_package(package_dir);self.options=runtime_options({'device':device,'deadline_ms':deadline_ms,'cpu_threads':cpu_threads})
        if device.startswith('openvino:'):raise ValueError('GAN generation package requires its verified PyTorch device')
        from backend.engine.runtime_deadline import CancellableExecution
        self._execution = CancellableExecution()

    def cancel(self):
        return self._execution.cancel()

    def execute(self,request):
        with self._execution.running() as event:
            return self._execute(request, event)

    def _execute(self,request,cancel_event):
        import os
        import sys
        from backend.engine.runtime_deadline import execute_owned_process
        allowed={'output_dir','count','seed','source_image_path','regions','source_sha256'}
        if not isinstance(request,dict) or set(request)-allowed or not isinstance(request.get('output_dir'),str):raise ValueError('GeneratorExecutor requires explicit output_dir and known generation fields')
        if type(request.get('count',8)) is not int or not 1<=request.get('count',8)<=500:raise ValueError('Generation count must be an integer from 1 to 500')
        if type(request.get('seed',0)) is not int or request.get('seed',0)<0:raise ValueError('Generation seed must be a nonnegative integer')
        output=Path(request['output_dir']).expanduser()
        if output.exists() or any(p.is_symlink() for p in (output,*output.parents)) or output.resolve().is_relative_to(self.root):raise ValueError('Generation output must be a new owned directory outside the package')
        output.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.native-generation-',dir=output.parent) as temporary:
            staged=Path(temporary)/'candidate';request_path=Path(temporary)/'request.json';result_path=Path(temporary)/'result.json'
            request_path.write_text(json.dumps({**request,'output_dir':str(staged),'device':self.options['device']}),encoding='utf-8')
            bootstrap='from backend.engine.gan_package_runtime import generator_worker;generator_worker()'
            result=execute_owned_process([sys.executable,'-c',bootstrap,str(self.root),str(request_path),str(result_path)],deadline_ms=self.options['deadline_ms'],cwd=self.root,cancel_event=cancel_event,
                env={**os.environ,'PYTHONPATH':str(self.root),'OMP_NUM_THREADS':str(self.options['cpu_threads']),'MKL_NUM_THREADS':str(self.options['cpu_threads'])})
            if result['status'] in ('timeout','cancelled'):return {**result,'task':'defect_gan','output_state':'synthetic_unreviewed','quality_status':'unvalidated'}
            if result['returncode']!=0:raise RuntimeError('Owned generation failed: '+result['stderr'])
            def relocate(value):
                if isinstance(value,str) and value.startswith(str(staged)):return str(output.resolve())+value[len(str(staged)):]
                if isinstance(value,dict):return {key:relocate(item) for key,item in value.items()}
                if isinstance(value,list):return [relocate(item) for item in value]
                return value
            report=relocate(json.loads(result_path.read_text(encoding='utf-8')))
            for artifact in staged.glob('*.json'):artifact.write_text(json.dumps(relocate(json.loads(artifact.read_text(encoding='utf-8'))),indent=2)+'\n',encoding='utf-8')
            if output.exists():raise ValueError('Generation output appeared while inference was running')
            os.rename(staged,output)
            return report


def generator_worker():
    import sys
    import torch
    root,request,result=map(Path,sys.argv[1:4]);payload=json.loads(request.read_text(encoding='utf-8'))
    torch.set_num_threads(int(__import__('os').environ.get('OMP_NUM_THREADS','1')))
    output=payload.pop('output_dir');report=run_generator_package(root,output,**payload)
    result.write_text(json.dumps(report),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description='Generate unreviewed defect candidates from a verified package')
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--count',type=int,default=8);parser.add_argument('--seed',type=int,default=0);parser.add_argument('--device',default='cpu')
    parser.add_argument('--source-image',type=Path);parser.add_argument('--regions',type=Path);parser.add_argument('--source-sha256')
    args=parser.parse_args()
    try:
        regions = json.loads(args.regions.read_text(encoding='utf-8')) if args.regions else None
        result=run_generator_package(Path(__file__).resolve().parents[2],args.output,count=args.count,seed=args.seed,device=args.device,
            source_image_path=args.source_image,regions=regions,source_sha256=args.source_sha256)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (ValueError,OSError) as exc:parser.exit(2,f'Generation package error: {exc}\n')
