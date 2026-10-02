"""Verified OpenVINO IR with measured precision drift and full DAG execution."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import sys
import os
import tempfile
import threading
import time
import numpy as np
import torch
from torch import nn


def _ov():
    if 'openvino' not in sys.modules and sys.modules.get('pyarrow') is not None and sys.platform=='darwin':
        raise ValueError('OpenVINO conversion must use the isolated optimization worker on this macOS runtime')
    try:import openvino as ov
    except ImportError as exc:raise ValueError('Install the openvino runtime dependency on this target') from exc
    return ov


def available_openvino_devices():
    if 'openvino' not in sys.modules:
        from backend.engine.runtime_deadline import execute_owned_process
        python=os.environ.get('VISION_OPENVINO_PYTHON',sys.executable)
        code="import json,sys;sys.modules['pyarrow']=None;import openvino as ov;c=ov.Core();print(json.dumps({'backend':'openvino','version':ov.__version__,'devices':c.available_devices,'names':{d:str(c.get_property(d,'FULL_DEVICE_NAME')) for d in c.available_devices}}))"
        result=execute_owned_process([python,'-c',code],deadline_ms=15000)
        if result['status']=='timeout' or result.get('returncode')!=0:
            raise ValueError('OpenVINO device discovery failed; install a compatible openvino runtime in VISION_OPENVINO_PYTHON: '+result.get('stderr','timeout'))
        return json.loads(result['stdout'])
    ov=_ov();core=ov.Core()
    return {'backend':'openvino','version':ov.__version__,'devices':core.available_devices,
        'names':{device:str(core.get_property(device,'FULL_DEVICE_NAME')) for device in core.available_devices}}


def require_openvino_device(device):
    devices=available_openvino_devices()['devices']
    if device not in devices:raise ValueError(f'OpenVINO device {device} is unavailable; available devices: {devices}')
    return device


def _flatten(value):
    if isinstance(value,torch.Tensor):return [value]
    if isinstance(value,dict):return [tensor for item in value.values() for tensor in _flatten(item)]
    if isinstance(value,(tuple,list)):return [tensor for item in value for tensor in _flatten(item)]
    raise ValueError('Model outputs must be tensors or nested tensor containers')


def _tree(value):
    if isinstance(value,torch.Tensor):return {'type':'tensor','dtype':str(value.dtype).split('.')[-1]}
    if isinstance(value,dict):return {'type':'dict','items':[[str(key),_tree(item)] for key,item in value.items()]}
    if isinstance(value,(tuple,list)):return {'type':'tuple' if isinstance(value,tuple) else 'list','items':[_tree(item) for item in value]}
    raise ValueError('Unsupported model output contract')


def _restore(tree,outputs):
    if tree['type']=='tensor':return torch.from_numpy(np.array(next(outputs),copy=True)).to(getattr(torch,tree['dtype']))
    if tree['type']=='dict':return {key:_restore(item,outputs) for key,item in tree['items']}
    values=[_restore(item,outputs) for item in tree['items']]
    return tuple(values) if tree['type']=='tuple' else values


class _FlatModel(nn.Module):
    def __init__(self,model):super().__init__();self.model=model
    def forward(self,value):return tuple(_flatten(self.model(value)))


def _hash_array(value):return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


def convert_model_artifact(model,example,output_dir,*,precision='fp32',calibration=(),validation=(),device='CPU',cpu_threads=1,dynamic_spatial=False):
    if precision not in ('fp32','fp16','int8'):raise ValueError('precision must be fp32, fp16 or int8')
    require_openvino_device(device)
    from backend.engine.runtime_configuration import runtime_options
    runtime_options({'cpu_threads':cpu_threads})
    example=np.asarray(example,dtype=np.float32)
    if example.ndim!=4 or not np.isfinite(example).all():raise ValueError('Conversion needs finite NCHW sample tensors')
    calibration=[np.asarray(item,dtype=np.float32) for item in calibration]
    validation=[np.asarray(item,dtype=np.float32) for item in validation]
    if not validation:raise ValueError('Conversion requires explicit validation tensors to measure precision drift')
    for item in [*calibration,*validation]:
        if item.shape!=example.shape or not np.isfinite(item).all():raise ValueError('Calibration/validation tensors must match the model input contract')
    if precision=='int8':
        if not calibration:raise ValueError('INT8 requires real calibration tensors')
        if {_hash_array(item) for item in calibration}&{_hash_array(item) for item in validation}:
            raise ValueError('INT8 calibration and validation must use distinct samples')
    output=Path(output_dir)
    if output.exists():raise ValueError('Conversion output already exists')
    output.mkdir(parents=True)
    try:
        ov=_ov();model=model.cpu().eval()
        with torch.inference_mode():sample=model(torch.from_numpy(example));tree=_tree(sample)
        flat=_FlatModel(model).eval()
        ir=ov.convert_model(flat,example_input=torch.from_numpy(example))
        if precision=='int8':
            try:import nncf
            except ImportError as exc:raise ValueError('Install nncf for calibrated INT8 conversion') from exc
            ir=nncf.quantize(ir,nncf.Dataset(calibration),subset_size=len(calibration))
        shape=list(example.shape);shape[0]=ov.Dimension(1,32)
        if dynamic_spatial:shape[2]=ov.Dimension(1,2048);shape[3]=ov.Dimension(1,2048)
        ir.reshape({ir.input(0):ov.PartialShape(shape)})
        ov.save_model(ir,str(output/'model.xml'),compress_to_fp16=precision=='fp16')
        core=ov.Core();properties=_compile_properties(core,device,cpu_threads)
        compiled=core.compile_model(str(output/'model.xml'),device,properties)
        gaps=[];relative=[];disagreements=0;compared=0;latencies=[];reference_latencies=[]
        for tensor in validation:
            reference_started=time.perf_counter()
            with torch.inference_mode():reference=[item.detach().cpu().numpy() for item in _flatten(model(torch.from_numpy(tensor)))]
            reference_latencies.append((time.perf_counter()-reference_started)*1000)
            started=time.perf_counter();prediction=compiled(tensor);latencies.append((time.perf_counter()-started)*1000)
            for index,expected in enumerate(reference):
                actual=np.asarray(prediction[compiled.output(index)])
                if actual.shape!=expected.shape:raise ValueError('Converted model output shape differs from source')
                difference=np.abs(actual.astype(np.float64)-expected.astype(np.float64))
                if not np.isfinite(difference).all():raise ValueError('Converted model produced nonfinite validation evidence')
                gaps.append(float(difference.max(initial=0)));relative.append(float(difference.mean()))
                if actual.ndim>=2 and actual.shape[-1]>1:
                    disagreements+=int(np.count_nonzero(actual.argmax(-1)!=expected.argmax(-1)));compared+=int(np.prod(actual.shape[:-1]))
        metrics={'calibration_count':len(calibration),'validation_count':len(validation),'max_absolute_error':max(gaps),
            'mean_absolute_error':float(np.mean(relative)),'output_argmax_disagreement_fraction':disagreements/compared if compared else None,'output_argmax_axis':-1,
            'latency_mean_ms':float(np.mean(latencies)),'latency_p95_ms':float(np.percentile(latencies,95)),
            'reference_latency_mean_ms':float(np.mean(reference_latencies)),
            'measured_speedup':float(np.mean(reference_latencies))/float(np.mean(latencies)),
            'artifact_bytes':(output/'model.xml').stat().st_size+(output/'model.bin').stat().st_size}
        result={'schema_version':1,'precision':precision,'device':device,'input_shape':list(example.shape),
            'dynamic_spatial':dynamic_spatial,'output_tree':tree,'metrics':metrics,'quality_approved':False,
            'openvino_version':ov.__version__,'quantized_operation_count':sum(node.get_type_name()=='FakeQuantize' for node in ir.get_ops())}
        if precision=='int8' and result['quantized_operation_count']==0:raise ValueError('INT8 conversion produced no quantized operations')
        (output/'conversion.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        return result
    except Exception:
        shutil.rmtree(output,ignore_errors=True);raise


def _compile_properties(core,device,threads):
    supported={str(value) for value in core.get_property(device,'SUPPORTED_PROPERTIES')}
    properties={}
    if 'NUM_STREAMS' in supported:properties['NUM_STREAMS']='1'
    # CPU FP16 means compressed weights; CPU comparison uses explicit FP32 compute.
    if device=='CPU':
        properties['INFERENCE_NUM_THREADS']=threads
        if 'INFERENCE_PRECISION_HINT' in supported:properties['INFERENCE_PRECISION_HINT']='f32'
    return properties


class CompiledModel(nn.Module):
    def __init__(self,directory,device,threads):
        super().__init__();self._lock=threading.Lock()
        metadata=json.loads((directory/'conversion.json').read_text(encoding='utf-8'));self.tree=metadata['output_tree']
        core=_ov().Core()
        self.compiled=core.compile_model(str(directory/'model.xml'),device,_compile_properties(core,device,threads))
    def forward(self,value):
        if not isinstance(value,torch.Tensor) or not torch.isfinite(value).all():raise ValueError('OpenVINO requires a finite tensor input')
        array=value.detach().cpu().contiguous().numpy()
        with self._lock:
            result=self.compiled(array)
            return _restore(self.tree,iter([result[self.compiled.output(i)] for i in range(len(self.compiled.outputs))]))


class OpenVINOSession:
    """Specialist loader hooks are scoped to the owned inference worker."""
    def __init__(self,package,device,threads):
        from backend.engine.flow_package_runtime import verify_flow_package,_sha256
        verify_flow_package(package);require_openvino_device(device)
        self.root=Path(package);self.device=device;self.threads=threads;self.cache={};self.hooks=[]
        self.records=json.loads((self.root/'openvino_models.json').read_text(encoding='utf-8'))['models']
        self.records={str((self.root/row['checkpoint']).resolve()):row for row in self.records}
        for checkpoint,row in self.records.items():
            if _sha256(Path(checkpoint))!=row['checkpoint_sha256']:raise ValueError('OpenVINO source checkpoint changed')
    def model(self,model,checkpoint,role='model'):
        row=self.records.get(str(Path(checkpoint).resolve()))
        if row is None or row['role']!=role:raise ValueError('No verified OpenVINO artifact for this model/role')
        if str(checkpoint) not in self.cache:
            self.cache[str(checkpoint)]=CompiledModel(self.root/row['directory'],self.device,self.threads)
        return self.cache[str(checkpoint)]
    def __enter__(self):
        from backend.engine.model_runtime import active_model_runtime,runtime_model
        self.token=active_model_runtime.set(self)
        from backend.engine import rotation,enhancement,ocr,rotated_detection
        for module,name in [(rotation,'load_rotation_model'),(enhancement,'_load'),(ocr,'_load_model'),(rotated_detection,'_load_checkpoint')]:
            original=getattr(module,name)
            def load(*args,_original=original,**kwargs):
                values=_original(*args,**kwargs);checkpoint=args[0] if args else kwargs.get('checkpoint',kwargs.get('checkpoint_path'))
                return (runtime_model(values[0],checkpoint),*values[1:])
            setattr(module,name,load);self.hooks.append((module,name,original))
        return self
    def __exit__(self,*_):
        from backend.engine.model_runtime import active_model_runtime
        for module,name,original in self.hooks:setattr(module,name,original)
        active_model_runtime.reset(self.token)
    def receipt(self):return {'backend':'openvino','device':self.device,'compiled_models':len(self.cache),'statistics_backend':'torch_cpu','quality_approved':False}


class _DinoLogits(nn.Module):
    def __init__(self,detector):super().__init__();self.model=detector.model
    def forward(self,rgb):
        import torch.nn.functional as F
        value=(rgb-self.model.input_mean)/self.model.input_std
        patch=int(self.model.encoder.patch_embed.patch_size[0]);h,w=value.shape[-2:]
        value=F.pad(value,(0,(-w)%patch,0,(-h)%patch))
        tokens=self.model.encoder.forward_features(value)
        pooled=torch.cat((tokens[:,0],tokens[:,self.model.num_prefix_tokens:].mean(1)),1)
        return self.model.head(pooled).squeeze(-1)


def _reconstruct(checkpoint,task):
    payload=torch.load(checkpoint,map_location='cpu',weights_only=True);role='model';channels=3
    size=payload.get('image_size',[256,256]);size=[size,size] if type(size)is int else size
    if task=='rotation':
        from backend.engine.rotation import load_rotation_model
        model,payload=load_rotation_model(checkpoint,'cpu');size=[payload['image_size']]*2
    elif task=='enhancement':
        from backend.engine.enhancement import _load
        model,payload,_=_load(checkpoint,'cpu');size=[32,32]
    elif task=='ocr':
        from backend.engine.ocr import _load_model
        model,payload,_=_load_model(checkpoint,'cpu');channels=1;size=list(reversed(payload['image_size']))
    elif task=='rotated_detection':
        from backend.engine.rotated_detection import _load_checkpoint
        model,payload,_=_load_checkpoint(checkpoint,'cpu');size=[payload['image_size']]*2
    else:
        from backend.engine.exporter import load_checkpoint_and_reconstruct_model
        model,payload,detector=load_checkpoint_and_reconstruct_model(checkpoint)
        if task in ('anomaly','anomaly_detection'):
            if payload.get('detector_type')=='dino_synthetic':model=_DinoLogits(detector);role='dino_logits';size=[detector.patch_size]*2
            else:model=detector.feature_extractor;role='features'
    if not isinstance(size,list) or len(size)!=2 or any(type(v)is not int or not 1<=v<=2048 for v in size):raise ValueError('Model has no valid saved input dimensions')
    return model,payload,role,size,channels


def _image_tensor(path,size,channels,task=None):
    from backend.engine.industrial_adapters import read_image_safely_rgb
    import cv2
    pixels=read_image_safely_rgb(Path(path),max_dim=None)
    if task=='ocr':
        from PIL import Image
        from backend.engine.ocr import _prepare_image
        return _prepare_image(Image.fromarray(pixels),(size[1],size[0])).unsqueeze(0).numpy()
    if task=='rotation':
        from backend.engine.rotation import _tensor
        return _tensor(pixels,size[0]).unsqueeze(0).numpy()
    pixels=cv2.resize(pixels,tuple(size),interpolation=cv2.INTER_LINEAR)
    if channels==1:pixels=cv2.cvtColor(pixels,cv2.COLOR_RGB2GRAY)[:,:,None]
    return np.ascontiguousarray(pixels.transpose(2,0,1)[None].astype(np.float32)/255)


def optimize_flow_package(package_dir,*,output_dir,precision='fp32',calibration_images=(),validation_images=(),device='CPU',cpu_threads=1,cancel_event=None,input_receipt=None):
    """Conversion is owned by an isolated worker, never by the app's native libraries."""
    from backend.engine.flow_package_runtime import verify_flow_package
    from backend.engine.runtime_deadline import execute_owned_process
    verify_flow_package(Path(package_dir))
    output=Path(output_dir).expanduser()
    if output.exists() or output.resolve().is_relative_to(Path(package_dir).resolve()) or any(p.is_symlink() for p in (output,*output.parents)):
        raise ValueError('Optimization output must be a new owned directory outside the source package')
    output.parent.mkdir(parents=True,exist_ok=True)
    payload={'package_dir':str(package_dir),'precision':precision,
        'calibration_images':[str(p) for p in calibration_images],'validation_images':[str(p) for p in validation_images],
        'device':device,'cpu_threads':cpu_threads,'input_receipt':input_receipt}
    with tempfile.TemporaryDirectory(prefix='.vision-conversion-',dir=output.parent) as temporary:
        request=Path(temporary)/'request.json';result_file=Path(temporary)/'result.json'
        staged=Path(temporary)/'candidate';payload['output_dir']=str(staged)
        request.write_text(json.dumps(payload),encoding='utf-8')
        python=os.environ.get('VISION_OPENVINO_PYTHON',sys.executable)
        code="import sys;sys.modules['pyarrow']=None;from backend.engine.openvino_runtime import optimization_worker;optimization_worker()"
        result=execute_owned_process([python,'-c',code,str(request),str(result_file)],deadline_ms=3600000,
            env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2]),'OMP_NUM_THREADS':str(cpu_threads),'MKL_NUM_THREADS':str(cpu_threads)},cancel_event=cancel_event)
        if result['status']=='cancelled' or (cancel_event is not None and cancel_event.is_set()):
            raise InterruptedError('OpenVINO optimization cancelled; owned conversion process terminated')
        if result['status']=='timeout':raise ValueError('OpenVINO conversion exceeded its one hour owned worker budget')
        if result.get('returncode')!=0:raise ValueError('OpenVINO conversion failed: '+result.get('stderr',''))
        report=json.loads(result_file.read_text(encoding='utf-8'))
        if output.exists():raise ValueError('Optimization output appeared while conversion was running')
        os.rename(staged,output)
        report['package_path']=str(output.resolve())
        return report


def optimization_worker():
    request,result=map(Path,sys.argv[1:3])
    payload=json.loads(request.read_text(encoding='utf-8'))
    torch.set_num_threads(payload['cpu_threads'])
    result.write_text(json.dumps(_optimize_flow_package(**payload)),encoding='utf-8')


def _optimize_flow_package(package_dir,*,output_dir,precision='fp32',calibration_images=(),validation_images=(),device='CPU',cpu_threads=1,input_receipt=None):
    from backend.engine.flow_package_runtime import verify_flow_package,_sha256
    source=Path(package_dir).resolve();_,checkpoints=verify_flow_package(source)
    destination=Path(output_dir).expanduser()
    if destination.exists() or destination.resolve().is_relative_to(source) or any(p.is_symlink() for p in (destination,*destination.parents)):raise ValueError('Optimization output must be a new owned directory outside the source package')
    if not validation_images:raise ValueError('Optimization requires explicit validation images')
    calibration_images=[Path(p).resolve(strict=True) for p in calibration_images]
    validation_images=[Path(p).resolve(strict=True) for p in validation_images]
    if precision=='int8' and not calibration_images:raise ValueError('INT8 requires calibration images')
    if precision=='int8' and {_sha256(p) for p in calibration_images}&{_sha256(p) for p in validation_images}:raise ValueError('INT8 calibration and validation images must be distinct')
    input_hashes={str(p):_sha256(p) for p in [*calibration_images,*validation_images]}
    receipt_sha=_sha256(source/'manifest.json');destination.parent.mkdir(parents=True,exist_ok=True)
    source_manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    destination.mkdir()
    for row in [*source_manifest['files'],{'path':'manifest.json'}]:
        target=destination/row['path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/row['path'],target,follow_symlinks=False)
    try:
        manifest=json.loads((destination/'manifest.json').read_text(encoding='utf-8'));models=[]
        for row in manifest['models']:
            checkpoint=destination/row['checkpoint'];model,payload,role,size,channels=_reconstruct(checkpoint,row['task'])
            calibration=[_image_tensor(p,size,channels,row['task']) for p in calibration_images]
            validation=[_image_tensor(p,size,channels,row['task']) for p in validation_images]
            directory=checkpoint.parent/'openvino'
            result=convert_model_artifact(model,validation[0],directory,precision=precision,calibration=calibration,validation=validation,
                device=device,cpu_threads=cpu_threads,dynamic_spatial=row['task']=='enhancement')
            result['metrics']['validation_image_count']=len(validation_images)
            (directory/'conversion.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
            record={'job_id':row['job_id'],'task':row['task'],'checkpoint':row['checkpoint'],'checkpoint_sha256':_sha256(checkpoint),
                'directory':directory.relative_to(destination).as_posix(),'role':role,**result}
            models.append(record)
        info={'schema_version':1,'source_manifest_sha256':receipt_sha,'models':models,'quality_approved':False,
              'calibration_sha256':[_sha256(p) for p in calibration_images],'validation_sha256':[_sha256(p) for p in validation_images],'input_receipt':input_receipt}
        (destination/'openvino_models.json').write_text(json.dumps(info,indent=2)+'\n',encoding='utf-8')
        config=json.loads((destination/'runtime_config.json').read_text(encoding='utf-8'));config.update({'device':'openvino:'+device,'cpu_threads':cpu_threads})
        (destination/'runtime_config.json').write_text(json.dumps(config,indent=2)+'\n',encoding='utf-8');manifest['runtime']=config
        requirements=(destination/'requirements.txt').read_text(encoding='utf-8');(destination/'requirements.txt').write_text(requirements+'openvino>=2026.0\n',encoding='utf-8')
        if 'deployment' in manifest:
            from backend.engine.edge_runtime import create_edge_profile
            target=manifest['deployment']['target']
            manifest['deployment']=create_edge_profile(target['os'],target['architecture'],(destination/'requirements.txt').read_text(encoding='utf-8'),device=config['device'])
            (destination/'edge_deployment.json').write_text(json.dumps(manifest['deployment'],indent=2)+'\n',encoding='utf-8')
        # A converted candidate must be approved separately; never inherit release approval.
        manifest.pop('release',None)
        manifest.pop('runtime_acceptance_sha256',None)
        (destination/'runtime_acceptance.json').unlink(missing_ok=True)
        def seal():
            manifest['files']=[{'path':p.relative_to(destination).as_posix(),'size':p.stat().st_size,'sha256':_sha256(p)}
                for p in sorted(destination.rglob('*')) if p.is_file() and p.name!='manifest.json' and '__pycache__' not in p.parts]
            (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
        seal()
        # Precision drift also needs the real downstream ROI/branch/rule effects.
        from backend.engine.flowchart_engine import FlowchartEngine
        from backend.engine.flow_package_runtime import run_flow_package,compare_flow_results
        pipeline=verify_flow_package(source)[0];heldout=[]
        for index,image in enumerate(validation_images):
            identity=f'heldout_{index:04d}'
            reference=FlowchartEngine(device='cpu',checkpoint_resolver=lambda job,_:checkpoints[job]).execute(pipeline=pipeline,image_path=image,image_id=identity)
            candidate=run_flow_package(destination,image,identity,device=config['device'],cpu_threads=cpu_threads,_owned_worker=True)
            comparison=compare_flow_results(reference,candidate)
            relative=f'heldout/{identity}.json';result_path=destination/relative;result_path.parent.mkdir(exist_ok=True)
            result_path.write_text(json.dumps({'image_sha256':_sha256(image),'reference':reference,'candidate':candidate,'comparison':comparison},indent=2)+'\n',encoding='utf-8')
            heldout.append({'image_sha256':_sha256(image),'result_path':relative,'result_sha256':_sha256(result_path),'comparison':comparison,
                'reference':{'final_verdict':reference['final_verdict'],'roi_count':reference['roi_count']},
                'candidate':{'final_verdict':candidate['final_verdict'],'roi_count':candidate['roi_count']}})
        (destination/'heldout_flow_results.json').write_text(json.dumps(heldout,indent=2)+'\n',encoding='utf-8')
        info.update(heldout_flow_results_sha256=_sha256(destination/'heldout_flow_results.json'),heldout_flow_count=len(heldout))
        (destination/'openvino_models.json').write_text(json.dumps(info,indent=2)+'\n',encoding='utf-8')
        seal()
        if _sha256(source/'manifest.json')!=receipt_sha:raise ValueError('Source package changed during optimization')
        if any(_sha256(Path(p))!=sha for p,sha in input_hashes.items()):raise ValueError('Calibration/validation source image changed during optimization')
        verify_flow_package(destination)
        return {'status':'success','package_path':str(destination),'models':models,'quality_approved':False,'source_manifest_sha256':receipt_sha,'candidate_manifest_sha256':_sha256(destination/'manifest.json'),
            'heldout_flow_count':len(heldout),'heldout_flow_passed_count':sum(row['comparison']['status']=='passed' for row in heldout)}
    except Exception:
        shutil.rmtree(destination,ignore_errors=True);raise
