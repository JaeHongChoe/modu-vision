"""Owned paired masks; synthetic geometry is not representative quality truth."""
import hashlib,json,os,sys
from pathlib import Path
import cv2
import numpy as np
from PIL import Image

def seed(root,project=None):
    root=Path(root).resolve();source=root/'dino-segmentation-inputs'
    if project is not None:
        assert Path(project['project_dir']).resolve().is_relative_to(root)
        split=Path(project['dataset_dir'])/'splits'/(hashlib.sha256(str(source).encode()).hexdigest()+'.json')
        assignments={p.relative_to(source).as_posix():p.relative_to(source).parts[0] for p in source.rglob('*.png')}
        split.parent.mkdir(parents=True,exist_ok=True);split.write_text(json.dumps({'folder_path':str(source),'assignments':assignments,'seed':112}))
        from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root
        from backend.engine.grouped_dataset_views import load_manifest_dataset
        from backend.engine.augmentations import IndustrialAugmentationPipeline,apply_sample_transform
        import torch
        token=set_request_split_root(split.parent)
        try:dataset=load_manifest_dataset('segmentation',source,'train',image_size=(64,64))
        finally:reset_request_split_root(token)
        assert dataset.classes==['background','scratch','stain'] and len(dataset)==2
        index=next(i for i,row in enumerate(dataset.samples) if row[0].name=='defect.png')
        image,mask=dataset[index];assert set(mask.unique().tolist())=={0,1,2}
        transform=IndustrialAugmentationPipeline(task='segmentation',brightness_range=(0,0),contrast_range=(0,0),max_rotation_deg=0,cutout_prob=0)
        result=apply_sample_transform(transform,image,mask,task='segmentation',seed=1)
        expected_image,expected_mask=image,mask
        if result.transform_receipt['horizontal_flip']:expected_image=expected_image.flip(-1);expected_mask=expected_mask.flip(-1)
        if result.transform_receipt['vertical_flip']:expected_image=expected_image.flip(-2);expected_mask=expected_mask.flip(-2)
        assert result.transform_receipt['horizontal_flip'] or result.transform_receipt['vertical_flip']
        assert torch.equal(result.image,expected_image) and torch.equal(result.targets,expected_mask)
        return {'split_path':str(split),'assignments':assignments,'mask_loader_classes':dataset.classes,'paired_flip_receipt':result.transform_receipt,'paired_flip_exact':True,'discrete_class_ids':result.targets.unique().tolist()}
    files=[]
    for part in ('train','val','test'):
        for defect in (False,True):
            mask=np.zeros((96,128),np.uint8);shapes=[]
            if defect:
                cv2.rectangle(mask,(20,12),(25,75),1,-1);cv2.fillPoly(mask,[np.array([[70,30],[103,33],[95,69],[68,58]],np.int32)],2)
                shapes=[{'label':'scratch','shape_type':'rectangle','points':[[20,12],[25,75]],'flags':{}},{'label':'stain','shape_type':'polygon','points':[[70,30],[103,33],[95,69],[68,58]],'flags':{}}]
            pixels=np.random.default_rng(112+len(files)).integers(95,145,(96,128,3),dtype=np.uint8);pixels[mask>0]=30
            image=source/part/('defect.png' if defect else 'background.png');image.parent.mkdir(parents=True,exist_ok=True);Image.fromarray(pixels).save(image)
            annotation=image.with_suffix('.json');annotation.write_text(json.dumps({'version':'5.0','imagePath':image.name,'imageWidth':128,'imageHeight':96,'imageData':None,'flags':{},'shapes':shapes}))
            files.append({'path':str(image),'annotation':str(annotation),'split':part,'defect':defect,'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'annotation_sha256':hashlib.sha256(annotation.read_bytes()).hexdigest()})
    weights=Path(os.environ['MV_E2E_DINO_WEIGHTS']).expanduser().absolute()
    from backend.engine.model_backbones import checkpoint_sha256
    return {'source':str(source),'files':files,'weights':str(weights),'weights_sha256':checkpoint_sha256(weights),'synthetic_only':True,'quality_approved':False}

if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]));print(json.dumps(seed(sys.argv[1],json.loads(sys.argv[2]) if len(sys.argv)>2 else None)))
