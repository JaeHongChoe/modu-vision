import numpy as np


def test_matching_counts_missing_and_duplicate_boxes_per_class():
    from backend.engine.evaluation_evidence import match_objects
    truth=[{'label':'scratch','box':[0,0,10,10]},{'label':'spot','box':[20,20,30,30]}]
    predicted=[{'label':'scratch','box':[0,0,10,10],'confidence':.9},{'label':'scratch','box':[0,0,10,10],'confidence':.8}]
    evidence=match_objects(predicted,truth)
    assert evidence['counts']=={'tp':1,'fp':1,'fn':1}
    assert evidence['per_class']['scratch']['fp']==1
    assert evidence['per_class']['spot']['fn']==1
    assert len(evidence['matches'])==1
    assert evidence['matches'][0]['iou']==1


def test_pixels_do_not_hide_second_defect_class():
    from backend.engine.evaluation_evidence import pixel_errors
    truth=np.array([[0,1,2],[2,2,0]],dtype=np.uint8)
    prediction=np.array([[0,1,0],[1,2,2]],dtype=np.uint8)
    result=pixel_errors(prediction,truth,['background','scratch','spot'])
    assert result['spot']['fn']==2 and result['spot']['fp']==1
    assert result['scratch']['tp']==1 and result['scratch']['fp']==1
    assert result['spot']['truth_area_px']==3


def test_ocr_aligns_unicode_missing_extra_and_substituted_characters():
    from backend.engine.evaluation_evidence import character_errors
    result=character_errors('A한B','AX한C!')
    assert result['edit_distance']==3
    assert result['counts']=={'correct':2,'substitution':1,'missing':0,'extra':2}
    assert result['per_character']['B']['fn']==1
    assert result['per_character']['C']['fp']==1
    assert result['per_character']['한']['tp']==1


def test_rotated_ap_is_ranked_and_penalizes_duplicate_false_positive():
    from backend.engine.evaluation_evidence import object_average_precision
    samples=[{'object_evidence':{'truth':[{'label':'x','box':[0,0,1,1]}],
        'predicted':[{'label':'x','box':[4,4,5,5],'confidence':.99},{'label':'x','box':[0,0,1,1],'confidence':.8}]}}]
    result=object_average_precision(samples)
    assert result['mAP_50']==.5
    assert result['class_ap50']=={'x':.5}


def test_distribution_roc_requires_both_truth_classes_and_uses_ranked_scores():
    from backend.engine.evaluation_evidence import evaluation_analysis
    samples=[{'file_path':'a','ground_truth':'OK','defect_score':.1}, {'file_path':'b','ground_truth':'NG','defect_score':.9}]
    result=evaluation_analysis(samples,'classification')
    assert result['roc']['auc']==1
    assert result['roc']['known_truth_count']==2
    assert sum(row['count'] for row in result['score_distribution'])==2
    assert evaluation_analysis(samples[1:],'classification')['roc']['available'] is False
