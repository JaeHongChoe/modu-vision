"""Authentic cached-file CPU updates and fresh offline reconstruction for all scopes."""
import gc,hashlib,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import torch
from backend.engine.model_backbones import DinoTaskModel,checkpoint_sha256
from backend.engine.exporter import load_checkpoint_and_reconstruct_model

def exercise(weights,root):
    weights=Path(weights).expanduser().absolute();root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    digest=checkpoint_sha256(weights);torch.set_num_threads(1);rows=[]
    for mode in ('head_only','partial','full'):
        torch.manual_seed(110)
        model=DinoTaskModel('classification','dinov3_vits16',3,True,str(weights),digest,mode,1)
        before={name:p.detach().clone() for name,p in model.named_parameters()}
        trainable=[name for name,p in model.named_parameters() if p.requires_grad]
        pixels=torch.rand(2,3,64,64);model.train()
        optimizer=torch.optim.SGD((p for p in model.parameters() if p.requires_grad),lr=.01)
        loss=torch.nn.functional.cross_entropy(model(pixels),torch.tensor([0,1]));loss.backward();optimizer.step()
        changed=[name for name,p in model.named_parameters() if not torch.equal(before[name],p)]
        assert changed and any(name.startswith('head.') for name in changed)
        assert all(name in trainable for name in changed)
        assert ('encoder.blocks.0.attn.qkv.weight' in changed)==(mode=='full')
        assert ('encoder.blocks.11.attn.qkv.weight' in changed)==(mode!='head_only')
        model.eval();expected=model(pixels).detach();checkpoint=root/(mode+'.pt');assert not checkpoint.exists()
        torch.save({'task':'classification','classes':['OK','scratch','stain'],'image_size':[64,64],'model_state_dict':model.state_dict(),**model.model_metadata},checkpoint)
        metadata=model.model_metadata
        del optimizer,model,before;gc.collect()
        rebuilt,_,_=load_checkpoint_and_reconstruct_model(checkpoint);actual=rebuilt.eval()(pixels).detach();torch.testing.assert_close(actual,expected)
        rows.append({'mode':mode,'loss':float(loss.detach()),'trainable_names':trainable,'changed_names':changed,'metadata':metadata,'checkpoint':str(checkpoint),'checkpoint_sha256':checkpoint_sha256(checkpoint),'offline_max_abs_difference':float((actual-expected).abs().max()),'frozen_parameters_unchanged':True})
        del rebuilt,loss;gc.collect()
    assert checkpoint_sha256(weights)==digest
    return {'device':'cpu','weights_sha256':digest,'actual_official_file_loaded_strictly':True,'actual_optimizer_steps':3,'scopes':rows,'gpu_verified':False,'quality_approved':False}

if __name__=='__main__':print(json.dumps(exercise(sys.argv[1],sys.argv[2])))
