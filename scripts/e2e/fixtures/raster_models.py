"""Fit owned synthetic PaDiM statistics with offline, untrained features.

This fixture exercises real production raster inference, not representative
model quality or the application's training workflow.
"""
import json
import sys
from pathlib import Path
import numpy as np
from PIL import Image
import torch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from backend.api import routes_dataset
from backend.engine.anomaly import PaDiMDetector
from backend.engine.dataset_fingerprint import fingerprint_dataset

source,models=(Path(arg).resolve() for arg in sys.argv[1:])
torch.set_num_threads(1);torch.manual_seed(42)
files=sorted((source/'train/good').glob('*.png'))
images=torch.stack([torch.from_numpy(np.asarray(Image.open(file).convert('RGB')).copy()).permute(2,0,1).float()/255 for file in files])
model=PaDiMDetector(pretrained=False,target_dim=4,device='cpu')
fit=model.fit(torch.utils.data.DataLoader(images,batch_size=2))
job=models/'job_fixture_raster';(job/'dataset').mkdir(parents=True,exist_ok=False)
fingerprint=fingerprint_dataset(source,studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,split_manifest=routes_dataset._split_manifest_file(source))
torch.save({'task':'anomaly','detector_type':'padim','feature_backbone':'resnet18','pretrained':False,'classes':['good','anomaly'],'image_size':[64,64],'model_state_dict':model.state_dict()},job/'best_model.pt')
(job/'model_meta.json').write_text(json.dumps({'task':'anomaly','detector_type':'padim'}))
(job/'job_receipt.json').write_text(json.dumps({'status':'completed','task':'anomaly','source_dataset_path':str(source),'dataset_fingerprint':fingerprint,'dataset_path':str(job/'dataset'),'test_fixture':'synthetic_statistics_untrained_features'}))
print(json.dumps({'checkpoint_kind':'synthetic_statistics_untrained_features','feature_pretrained':False,'normal_fit_images':len(files),'fit':fit}))
