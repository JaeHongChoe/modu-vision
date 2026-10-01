"""Evaluate only actual anomaly masks; absent defect masks never become normal truth."""
from __future__ import annotations
import cv2
import numpy as np
import torch
from backend.engine.anomaly.metrics import compute_anomaly_metrics


def evaluate_anomaly_dataset(model, dataset, *, device='cpu', cancellation_requested=None):
    scores=[];labels=[];heatmaps=[];masks=[];missing=0
    semantics = getattr(model, 'model_metadata', {}).get('map_semantics', 'pixel_score')
    with torch.inference_mode():
        for index in range(len(dataset)):
            if cancellation_requested is not None and cancellation_requested():
                from backend.engine.anomaly.cancellation import AnomalyFitCancelled
                raise AnomalyFitCancelled()
            image,label,mask=dataset[index]
            batch = image.unsqueeze(0)
            maps,batch_scores=model(batch.cpu() if semantics == 'patch_score' else batch.to(device))
            score=float(batch_scores.reshape(-1)[0].cpu())
            values=np.asarray(maps[0].detach().cpu() if isinstance(maps,torch.Tensor) else maps[0]).squeeze()
            target=np.asarray(mask.cpu() if isinstance(mask,torch.Tensor) else mask).squeeze()
            scores.append(score);labels.append(int(label))
            if semantics == 'patch_score':
                missing += 1
                continue
            source_sample=dataset.samples[index] if hasattr(dataset,'samples') else None
            known = int(label)==0 or (source_sample is not None and source_sample[2] is not None)
            if not known:
                missing+=1;continue
            if values.shape!=target.shape:
                values=cv2.resize(values,(target.shape[1],target.shape[0]),interpolation=cv2.INTER_LINEAR)
            heatmaps.append(values);masks.append(target)
    result=compute_anomaly_metrics(scores,labels,heatmaps,masks,
        fixed_threshold=getattr(model, 'threshold', None),
        threshold_comparison='gt' if semantics == 'patch_score' else 'ge')
    if len(set(labels)) < 2:
        result['image_auroc'] = None
    result.update(pixel_evaluated_images=len(masks),pixel_missing_masks=missing,sample_count=len(scores),map_semantics=semantics)
    return result
