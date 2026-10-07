"""Bounded optional ONNX CUDA provider qualification on one reserved GPU.

Synthetic heldout tensors and a saved synthetic image qualify execution only.
Run from an owned offline dependency environment; no downloads, activation,
training, publisher signature or representative model-quality approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--gpu-uuid',required=True)
    args=parser.parse_args()
    import torch
    import numpy as np
    import onnx
    from onnx import helper,numpy_helper,TensorProto
    import onnxruntime as ort
    from PIL import Image
    torch.set_num_threads(1)
    if not args.gpu_uuid.startswith('GPU-') or os.environ.get('NVIDIA_VISIBLE_DEVICES')!=args.gpu_uuid:
        raise ValueError('An explicitly reserved physical NVIDIA UUID is required')
    if not torch.cuda.is_available() or torch.cuda.device_count()!=1:
        raise ValueError('Exactly one reserved CUDA device is required; no CPU fallback')
    if 'CUDAExecutionProvider' not in ort.get_available_providers():
        raise ValueError('The offline CUDA provider is unavailable; no CPU fallback')
    observed=subprocess.check_output(['nvidia-smi','--id='+args.gpu_uuid,
        '--query-gpu=uuid,name,driver_version','--format=csv,noheader'],text=True).strip().split(', ')
    if len(observed)!=3 or observed[0]!=args.gpu_uuid:raise ValueError('Physical GPU identity differs')
    args.output.mkdir(parents=True,exist_ok=False)
    torch.manual_seed(83)
    model=torch.nn.Sequential(torch.nn.Conv2d(3,4,3,padding=1),torch.nn.ReLU(),
        torch.nn.AdaptiveAvgPool2d(1),torch.nn.Flatten(),torch.nn.Linear(4,2)).eval()
    image=args.output/'known-image.png'
    Image.fromarray(np.random.default_rng(7).integers(0,256,(16,16,3),dtype=np.uint8)).save(image)
    image_sha=hashlib.sha256(image.read_bytes()).hexdigest()
    samples=[np.random.default_rng(i).random((1,3,16,16),dtype=np.float32) for i in (41,42)]
    with Image.open(image) as saved:
        samples.append(np.asarray(saved.convert('RGB'),dtype=np.float32).transpose(2,0,1)[None]/255)
    precisions=[];original_files={}
    for precision,dtype,kind in [('fp32',np.float32,TensorProto.FLOAT),('fp16',np.float16,TensorProto.FLOAT16)]:
        conv,linear=model[0],model[4]
        tensors={'conv_weight':conv.weight.detach().numpy(),'conv_bias':conv.bias.detach().numpy(),
            'linear_weight':linear.weight.detach().numpy(),'linear_bias':linear.bias.detach().numpy()}
        graph=helper.make_graph([
            helper.make_node('Conv',['input','conv_weight','conv_bias'],['conv'],pads=[1,1,1,1]),
            helper.make_node('Relu',['conv'],['relu']),helper.make_node('GlobalAveragePool',['relu'],['pool']),
            helper.make_node('Flatten',['pool'],['flat'],axis=1),
            helper.make_node('Gemm',['flat','linear_weight','linear_bias'],['output'],transB=1)],
            'owned_optional_cuda_control',[helper.make_tensor_value_info('input',kind,[1,3,16,16])],
            [helper.make_tensor_value_info('output',kind,[1,2])],
            initializer=[numpy_helper.from_array(v.astype(dtype),k) for k,v in tensors.items()])
        artifact=helper.make_model(graph,opset_imports=[helper.make_opsetid('',13)],ir_version=9)
        onnx.checker.check_model(artifact)
        path=args.output/(precision+'.onnx');onnx.save(artifact,path)
        original_files[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
        options.add_session_config_entry('session.disable_cpu_ep_fallback','1')
        options.enable_profiling=True;options.profile_file_prefix=str(args.output/(precision+'-profile'))
        session=ort.InferenceSession(str(path),sess_options=options,providers=[('CUDAExecutionProvider',
            {'device_id':0,'gpu_mem_limit':256*1024**2,'use_tf32':0,'cudnn_conv_algo_search':'HEURISTIC'})])
        if session.get_providers()!=['CUDAExecutionProvider']:raise ValueError('Session enabled a CPU fallback provider')
        reference=model if precision=='fp32' else __import__('copy').deepcopy(model).half().cuda()
        errors=[];mismatches=0
        for sample in samples:
            value=sample.astype(dtype)
            tensor=torch.from_numpy(value)
            if precision=='fp16':tensor=tensor.cuda()
            with torch.inference_mode():expected=reference(tensor).float().cpu().numpy()
            actual=session.run(None,{'input':value})[0].astype(np.float32)
            tolerance=2e-6 if precision=='fp32' else 2e-3
            np.testing.assert_allclose(actual,expected,rtol=tolerance,atol=tolerance)
            errors.append(float(np.max(np.abs(actual-expected))))
            mismatches+=int(np.argmax(actual)!=np.argmax(expected))
        value=samples[0].astype(dtype)
        for _ in range(3):session.run(None,{'input':value})
        durations=[]
        for _ in range(10):
            started=time.perf_counter();session.run(None,{'input':value});durations.append(time.perf_counter()-started)
        profile=Path(session.end_profiling());events=json.loads(profile.read_text())
        kernels=[e for e in events if e.get('cat')=='Node' and e.get('args',{}).get('provider')]
        providers=sorted({e['args']['provider'] for e in kernels})
        if not kernels or providers!=['CUDAExecutionProvider']:
            raise ValueError('Actual node execution was not exclusively CUDA')
        precisions.append({'precision':precision,'heldout_tensor_count':2,'known_image_count':1,
            'max_absolute_error':max(errors),'argmax_mismatch_count':mismatches,
            'node_execution_providers':providers,'profile_kernel_count':len(kernels),
            'profile_sha256':hashlib.sha256(profile.read_bytes()).hexdigest(),
            'artifact_sha256':original_files[path.name],'warmup_count':3,'timing_iterations':10,
            'median_seconds':float(np.median(durations)),'p95_seconds':float(np.quantile(durations,.95)),
            'cpu_fallback_disabled':True})
        del session,reference
    assert hashlib.sha256(image.read_bytes()).hexdigest()==image_sha
    assert all(hashlib.sha256((args.output/name).read_bytes()).hexdigest()==sha for name,sha in original_files.items())
    receipt={'schema':'modu-vision.optional-nvidia-runtime-control/v1','actual_cuda':True,
        'physical_gpu_uuid':observed[0],'device_name':observed[1],'driver_version':observed[2],
        'torch_version':str(torch.__version__),'torch_cuda_version':torch.version.cuda,
        'onnxruntime_version':ort.__version__,'cuda_device_count':torch.cuda.device_count(),
        'peak_torch_allocated_bytes':torch.cuda.max_memory_allocated(),'precision_controls':precisions,
        'image_sha256':image_sha,'original_artifact_bytes_preserved':True,
        'source_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'quality_approved':False,'studio_job_verified':False,'physical_service_accepted':False,
        'release_approved':False,'scope':'Actual isolated offline CUDA provider control; synthetic data and tiny graphs only'}
    with (args.output/'receipt.json').open('x') as stream:json.dump(receipt,stream,indent=2);stream.write('\n')
    print(json.dumps(receipt))


if __name__=='__main__':main()
