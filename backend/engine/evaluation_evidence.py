"""Object, pixel and character evidence with explicit metric grain."""
from collections import Counter,defaultdict
import math
import numpy as np


def _iou(a,b):
    if isinstance(a,dict) and isinstance(b,dict):
        from backend.engine.rotated_detection import oriented_iou
        return float(oriented_iou(a,b))
    if len(a)!=4 or len(b)!=4:raise ValueError('Object boxes need four coordinates')
    a=list(map(float,a));b=list(map(float,b))
    if not all(math.isfinite(v) for v in a+b) or a[2]<a[0] or a[3]<a[1] or b[2]<b[0] or b[3]<b[1]:raise ValueError('Invalid object coordinates')
    intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
    return intersection/union if union else 0.


def _counts(tp,fp,fn):
    precision=tp/(tp+fp) if tp+fp else 0.
    recall=tp/(tp+fn) if tp+fn else 0.
    return {'tp':int(tp),'fp':int(fp),'fn':int(fn),'precision':precision,'recall':recall,
            'f1':2*precision*recall/(precision+recall) if precision+recall else 0.}


def match_objects(predicted,truth,*,iou_threshold=.5,confidence_threshold=.5):
    if not 0<=iou_threshold<=1 or not 0<=confidence_threshold<=1:raise ValueError('Matching thresholds require [0,1]')
    labels={str(row['label']) for row in predicted+truth}
    per_class={name:{'tp':0,'fp':0,'fn':0} for name in labels}
    used=set();matches=[];extra=[]
    selected=[]
    for index,row in enumerate(predicted):
        score=float(row.get('confidence',1))
        if not math.isfinite(score) or not 0<=score<=1:raise ValueError('Object confidence requires [0,1]')
        if score>=confidence_threshold:selected.append((index,row))
    for index,row in sorted(selected,key=lambda pair:float(pair[1].get('confidence',1)),reverse=True):
        choices=[(_iou(row['box'],target['box']),j) for j,target in enumerate(truth) if j not in used and str(target['label'])==str(row['label'])]
        overlap,j=max(choices,default=(-1,-1))
        label=str(row['label'])
        if j>=0 and overlap>=iou_threshold:
            used.add(j);per_class[label]['tp']+=1
            match={'prediction_index':index,'truth_index':j,'label':label,'iou':overlap,'confidence':float(row.get('confidence',1))}
            if isinstance(row['box'],dict):
                match['angle_error_deg']=abs((row['box']['angle_deg']-truth[j]['box']['angle_deg']+90)%180-90)
            matches.append(match)
        else:extra.append(index);per_class[label]['fp']+=1
    missing=[i for i in range(len(truth)) if i not in used]
    for i in missing:per_class[str(truth[i]['label'])]['fn']+=1
    return {'grain':'object','iou_threshold':iou_threshold,'confidence_threshold':confidence_threshold,
            'predicted':predicted,'truth':truth,'matches':matches,'extra_prediction_indices':extra,
            'missing_truth_indices':missing,'counts':{'tp':len(matches),'fp':len(extra),'fn':len(missing)},
            'per_class':{name:_counts(**values) for name,values in per_class.items()}}


def object_average_precision(samples):
    evidence=[row.get('object_evidence',row) for row in samples]
    labels=sorted({str(target['label']) for row in evidence for target in row.get('truth',[])})
    def ap(label,threshold):
        targets={index:[target for target in row.get('truth',[]) if str(target['label'])==label] for index,row in enumerate(evidence)}
        total=sum(map(len,targets.values()));used=defaultdict(set)
        predictions=[(float(p.get('confidence',1)),index,p) for index,row in enumerate(evidence) for p in row.get('predicted',[]) if str(p['label'])==label]
        tp=[];fp=[]
        for score,index,p in sorted(predictions,key=lambda row:row[0],reverse=True):
            if not math.isfinite(score) or not 0<=score<=1:raise ValueError('Object confidence requires [0,1]')
            choices=[(_iou(p['box'],target['box']),j) for j,target in enumerate(targets[index]) if j not in used[index]]
            overlap,j=max(choices,default=(-1,-1));matched=j>=0 and overlap>=threshold
            if matched:used[index].add(j)
            tp.append(int(matched));fp.append(int(not matched))
        if not tp:return 0.
        true=np.cumsum(tp);false=np.cumsum(fp);recall=true/total;precision=true/(true+false)
        r=np.concatenate(([0.],recall,[1.]));p=np.concatenate(([0.],precision,[0.]))
        for i in range(len(p)-2,-1,-1):p[i]=max(p[i],p[i+1])
        indices=np.where(r[1:]!=r[:-1])[0]
        return float(np.sum((r[indices+1]-r[indices])*p[indices+1]))
    curves={label:{f'{threshold:.2f}':ap(label,threshold) for threshold in np.arange(.5,.96,.05)} for label in labels}
    return {'grain':'object','mAP_50':float(np.mean([row['0.50'] for row in curves.values()])) if curves else None,
            'mAP_50_95':float(np.mean([list(row.values()) for row in curves.values()])) if curves else None,
            'class_ap50':{label:row['0.50'] for label,row in curves.items()},'class_ap_by_iou':curves,
            'evaluated_truth_objects':sum(len(row.get('truth',[])) for row in evidence)}


def pixel_errors(prediction,truth,classes):
    prediction=np.asarray(prediction);truth=np.asarray(truth)
    if prediction.shape!=truth.shape or prediction.ndim!=2:raise ValueError('Pixel evaluation masks require equal 2D geometry')
    if any(np.any((mask<0)|(mask>=len(classes))) for mask in (prediction,truth)):raise ValueError('Pixel mask class is outside the class mapping')
    result={}
    for index,name in enumerate(classes):
        actual=truth==index;predicted=prediction==index
        tp=int((actual&predicted).sum());fp=int((~actual&predicted).sum());fn=int((actual&~predicted).sum())
        result[name]={**_counts(tp,fp,fn),'class_id':index,'truth_area_px':int(actual.sum()),'predicted_area_px':int(predicted.sum()),
                      'iou':tp/(tp+fp+fn) if tp+fp+fn else None,'grain':'pixel'}
    return result


def character_errors(reference,predicted):
    if not isinstance(reference,str) or not isinstance(predicted,str):raise ValueError('Character evaluation requires actual strings')
    if len(reference)>10000 or len(predicted)>10000 or len(reference)*len(predicted)>4_000_000:raise ValueError('Character alignment input is too large')
    table=np.empty((len(reference)+1,len(predicted)+1),dtype=np.int32)
    table[:,0]=np.arange(len(reference)+1);table[0,:]=np.arange(len(predicted)+1)
    for i,a in enumerate(reference,1):
        for j,b in enumerate(predicted,1):table[i,j]=min(table[i-1,j]+1,table[i,j-1]+1,table[i-1,j-1]+(a!=b))
    i=len(reference);j=len(predicted);alignment=[];counts=Counter();chars=defaultdict(lambda:{'tp':0,'fp':0,'fn':0})
    while i or j:
        if i and j and table[i,j]==table[i-1,j-1]+(reference[i-1]!=predicted[j-1]):
            actual=reference[i-1];guess=predicted[j-1];operation='correct' if actual==guess else 'substitution'
            alignment.append({'operation':operation,'reference_index':i-1,'prediction_index':j-1,'reference':actual,'predicted':guess});counts[operation]+=1
            if operation=='correct':chars[actual]['tp']+=1
            else:chars[actual]['fn']+=1;chars[guess]['fp']+=1
            i-=1;j-=1
        elif i and table[i,j]==table[i-1,j]+1:
            actual=reference[i-1];alignment.append({'operation':'missing','reference_index':i-1,'prediction_index':None,'reference':actual,'predicted':None});counts['missing']+=1;chars[actual]['fn']+=1;i-=1
        else:
            guess=predicted[j-1];alignment.append({'operation':'extra','reference_index':None,'prediction_index':j-1,'reference':None,'predicted':guess});counts['extra']+=1;chars[guess]['fp']+=1;j-=1
    return {'grain':'unicode_codepoint','edit_distance':int(table[-1,-1]),'reference_characters':len(reference),
            'character_error_rate':float(table[-1,-1])/len(reference) if reference else (0. if not predicted else None),
            'counts':{key:counts[key] for key in ('correct','substitution','missing','extra')},
            'alignment':list(reversed(alignment)),'per_character':{char:_counts(**value) for char,value in chars.items()}}


def evaluation_analysis(samples,task,roles=None):
    from backend.engine.evaluation_history import binary_verdict
    bins=[{'lower':i/10,'upper':(i+1)/10,'count':0,'file_paths':[]} for i in range(10)]
    scores=[];sizes=[];classes=defaultdict(lambda:{'tp':0,'fp':0,'fn':0})
    for row in samples:
        score=row.get('defect_score',row.get('confidence'))
        if isinstance(score,(int,float)) and math.isfinite(score) and 0<=score<=1:
            bucket=bins[min(9,int(score*10))];bucket['count']+=1;bucket['file_paths'].append(row.get('file_path'))
        defect_score=row.get('defect_score')
        actual=binary_verdict(row.get('ground_truth_verdict',row.get('ground_truth')),roles)
        if task in ('classification','patch_classification','detection','segmentation','anomaly') and actual and isinstance(defect_score,(int,float)) and math.isfinite(defect_score) and 0<=defect_score<=1:
            scores.append((float(defect_score),actual=='NG'))
        object_evidence=row.get('object_evidence') or {}
        for box in object_evidence.get('predicted',[]):
            b=box['box'];area=float(b['width']*b['height']) if isinstance(b,dict) else max(0,(b[2]-b[0])*(b[3]-b[1]))
            sizes.append({'file_path':row.get('file_path'),'class':box['label'],'area':area,'unit':'original_image_px2'})
        for name,counts in (row.get('pixel_evidence') or {}).get('per_class',{}).items():
            if counts.get('class_id',0)>0:sizes.append({'file_path':row.get('file_path'),'class':name,'area':counts['predicted_area_px'],'unit':'model_input_px2'})
        maps=[object_evidence.get('per_class',{}),(row.get('pixel_evidence') or {}).get('per_class',{}),(row.get('character_evidence') or {}).get('per_character',{})]
        for mapping in maps:
            for name,counts in mapping.items():
                for key in ('tp','fp','fn'):classes[name][key]+=int(counts.get(key,0))
    positive=sum(truth for _,truth in scores);negative=len(scores)-positive
    roc={'available':bool(positive and negative),'known_truth_count':len(scores),'positive_count':positive,'negative_count':negative,'points':[],'auc':None}
    if roc['available']:
        points=[]
        for threshold in [1.0000001,*sorted({score for score,_ in scores},reverse=True),0.]:
            tp=sum(truth and score>=threshold for score,truth in scores);fp=sum(not truth and score>=threshold for score,truth in scores)
            points.append({'threshold':threshold,'tpr':tp/positive,'fpr':fp/negative,'tp':tp,'fp':fp,'fn':positive-tp,'tn':negative-fp})
        roc.update(points=points,auc=float(sum((b['fpr']-a['fpr'])*(a['tpr']+b['tpr'])/2 for a,b in zip(points,points[1:]))))
    else:roc['reason']='ROC requires actual positive and negative truth with bound defect scores'
    return {'sample_grain':'patch' if task=='patch_classification' else 'image','score_distribution':bins,
            'score_semantics':'defect_score when available, otherwise model confidence',
            'area_samples':sizes,'per_class_errors':{name:_counts(**counts) for name,counts in classes.items()},'roc':roc}
