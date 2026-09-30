"""Native-pixel inference for every measured specialist on the owned worker."""
from pathlib import Path
from types import SimpleNamespace
import time
import numpy as np
from backend.engine.dicom_input import open_source_image


def infer_specialist(task,checkpoint,image,*,device,threshold,output_dir):
    start=time.perf_counter()
    with open_source_image(image) as opened:rgb=np.asarray(opened.convert('RGB')).copy()
    overlay=rgb;confidence=None
    if task=='rotation':
        from backend.engine.rotation import predict_rotation_array
        predictions=predict_rotation_array(checkpoint,rgb,device=device)
        overlay=predictions.pop('aligned_image');predictions['transform']=predictions['transform'].tolist()
    elif task=='ocr':
        from backend.engine.ocr import predict_ocr_array
        predictions=predict_ocr_array(checkpoint,rgb,device=device);confidence=predictions['confidence']
    elif task=='rotated_detection':
        from backend.engine.rotated_detection import predict_rotated_array
        predictions=predict_rotated_array(checkpoint,rgb,device=device,threshold=threshold)
        import cv2
        overlay=rgb.copy()
        for row in predictions.get('predictions',[]):
            polygon=row.get('polygon') or row.get('corners')
            if polygon:cv2.polylines(overlay,[np.round(polygon).astype(np.int32)],True,(0,255,255),2)
    elif task=='enhancement':
        from backend.engine.enhancement import predict_enhancement
        overlay=predict_enhancement(checkpoint,rgb,device=device)
        predictions={'task':task,'source_size':[rgb.shape[1],rgb.shape[0]],'output_size':[overlay.shape[1],overlay.shape[0]]}
    elif task=='defect_gan':
        from backend.engine.defect_gan import generate_defect_candidates
        review=generate_defect_candidates(checkpoint,Path(output_dir)/'generated',count=1,seed=0,device=device)
        with open_source_image(review['candidates'][0]['path']) as generated:overlay=np.asarray(generated.convert('RGB')).copy()
        predictions={key:value for key,value in review.items() if key!='candidates'}
        predictions['candidates']=[{key:value for key,value in row.items() if key!='path'} for row in review['candidates']]
        predictions.update(task=task,review_state='pending',automatically_approved=False)
    else:raise ValueError('Unsupported worker specialist inference')
    return SimpleNamespace(predictions=predictions,visual_overlay=overlay,confidence_score=confidence,latency_ms=(time.perf_counter()-start)*1000)
