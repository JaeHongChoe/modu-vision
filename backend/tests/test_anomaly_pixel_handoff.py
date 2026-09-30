import torch
from backend.engine.anomaly.evaluation import evaluate_anomaly_dataset


def test_pixel_evaluation_excludes_unlabeled_defect_masks():
    class Data:
        samples=[('good',0,None),('scratch',1,'mask'),('missing',1,None)]
        def __len__(self): return 3
        def __getitem__(self,i):
            mask=torch.zeros((4,4),dtype=torch.long)
            if i==1:mask[1:3,1:3]=1
            return torch.full((3,4,4),float(i)),self.samples[i][1],mask
    class Model:
        def __call__(self,image):
            i=int(image[0,0,0,0]);heat=torch.zeros((1,4,4))
            if i:heat[:,1:3,1:3]=0.9
            return heat,torch.tensor([0.1 if i==0 else 0.9])
    metrics=evaluate_anomaly_dataset(Model(),Data())
    assert metrics['pixel_auroc']==1
    assert metrics['pixel_evaluated_images']==2
    assert metrics['pixel_missing_masks']==1
