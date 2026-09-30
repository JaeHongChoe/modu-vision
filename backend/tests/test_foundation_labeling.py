"""The adapter boundary is controlled; geometry, filtering and fitting are real."""
import base64
import io
import threading

import numpy as np
import pytest
from PIL import Image

from backend.engine import label_candidate_providers as providers


def test_foundation_requires_real_model_prerequisite(tmp_path):
    image = tmp_path / 'image.png'
    Image.new('RGB', (32, 24)).save(image)
    with pytest.raises(ValueError, match='SAM2'):
        providers.foundation_candidates(image, {}, points=[{'x': 10, 'y': 8, 'label': 1}])


def test_foundation_mask_keeps_original_coordinates_and_holes(tmp_path, monkeypatch):
    from backend.engine import foundation_labeling as foundation
    image = tmp_path / 'image.png'
    Image.new('RGB', (30, 20)).save(image)
    mask = np.zeros((20, 30), dtype=bool)
    mask[3:12, 7:16] = True
    mask[6:8, 10:12] = False

    class MaskModel:
        provenance = {'provider': 'sam2', 'model_sha256': 'verified-test-boundary'}
        def predict(self, rgb, points, box, cancel):
            assert rgb.size == (30, 20)
            assert points == [{'x': 8, 'y': 4, 'label': 1}, {'x': 10, 'y': 6, 'label': 0}]
            assert box == [5., 2., 20., 15.]
            return mask, .91

    monkeypatch.setattr(foundation, '_load_sam2', lambda setup, device: MaskModel())
    result = providers.foundation_candidates(image, {}, label='part', threshold=.8,
        points=[{'x': 8, 'y': 4, 'label': 1}, {'x': 10, 'y': 6, 'label': 0}],
        boxes=[[5., 2., 20., 15.]], output_geometry='mask', device='cpu')
    assert len(result) == 1
    candidate = result[0]
    assert candidate['annotation']['bbox'] == [7., 3., 16., 12.]
    assert candidate['area'] == 77
    assert candidate['source'] == 'sam2'
    png = candidate['annotation']['mask_rle'].split(',', 1)[1]
    alpha = np.asarray(Image.open(io.BytesIO(base64.b64decode(png))))[..., 3]
    np.testing.assert_array_equal(alpha > 0, mask)


def test_foundation_filters_size_and_rejects_outside_prompts(tmp_path, monkeypatch):
    from backend.engine import foundation_labeling as foundation
    image = tmp_path / 'image.png'
    Image.new('RGB', (30, 20)).save(image)
    class MaskModel:
        provenance = {'provider': 'sam2'}
        def predict(self, rgb, points, box, cancel):
            mask = np.zeros((20, 30), bool); mask[3:8, 7:11] = 1
            return mask, .9
    monkeypatch.setattr(foundation, '_load_sam2', lambda setup, device: MaskModel())
    assert providers.foundation_candidates(image, {}, boxes=[[2, 2, 20, 15]], min_area=21) == []
    with pytest.raises(ValueError, match='outside'):
        providers.foundation_candidates(image, {}, points=[{'x': 30, 'y': 8, 'label': 1}])


def test_long_prompt_chunks_preserve_all_text_without_truncation():
    from backend.engine import foundation_labeling as foundation
    text = ' '.join('object%05d'%i for i in range(1200))
    chunks = foundation.prompt_chunks(text, max_words=60)
    assert len(chunks) == 20
    assert ' '.join(chunks) == text


def test_negative_visual_examples_remove_matching_proposals():
    from backend.engine import foundation_labeling as foundation
    scores, keep = foundation.visual_scores(np.array([[1., 0.], [0., 1.]]),
        np.array([[1., 0.]]), np.array([[0., 1.]]))
    assert keep.tolist() == [True, False]
    assert scores[0] == pytest.approx(1.)


def test_feature_classifier_learns_manual_labels_and_cancel_preserves_parent():
    from backend.engine import foundation_labeling as foundation
    import torch
    features = torch.tensor([[1., 0.], [.9, .1], [0., 1.], [.1, .9]])
    targets = torch.tensor([0, 0, 1, 1])
    state, loss = foundation.fit_feature_classifier(features, targets, 2, epochs=60, learning_rate=.1)
    logits = features @ state['weight'].T + state['bias']
    assert logits.argmax(1).tolist() == [0, 0, 1, 1]
    assert loss[-1] < loss[0]
    before = {key: value.clone() for key, value in state.items()}
    cancel = threading.Event(); cancel.set()
    with pytest.raises(foundation.LabelingCancelled):
        foundation.fit_feature_classifier(features, targets, 2, parent_state=state, cancel=cancel)
    for key in before:
        assert torch.equal(state[key], before[key])


def test_region_size_filter_uses_polygon_area_not_its_bounding_rectangle():
    candidates=[{'confidence':.9,'annotation':{'type':'polygon','label':'triangle',
        'polygon':[[0.,0.],[10.,0.],[0.,10.]]}}]
    assert providers.filter_candidate_sizes(candidates,min_area=60)==[]
    assert providers.filter_candidate_sizes(candidates,min_area=50,max_area=50)==candidates


def test_combined_text_and_visual_prompt_uses_both_gates(tmp_path,monkeypatch):
    from backend.engine import foundation_labeling as foundation
    import torch
    image=tmp_path/'image.png';Image.new('RGB',(30,20)).save(image)
    class Model:
        provenance={'provider':'sam2'}
        def predict(self,rgb,points,box,cancel):
            assert box==[2.,2.,15.,15.]
            mask=np.zeros((20,30),bool);mask[3:8,7:11]=1
            return mask,.95
    class Encoder:
        model_metadata={'pretrained_sha256':'featuretest','backbone':'dinov3_vits16'}
    monkeypatch.setattr(foundation,'_load_sam2',lambda *args:Model())
    monkeypatch.setattr(providers,'grounded_candidates',lambda *args,**kwargs:[{
        'confidence':.8,'annotation':{'bbox':[2.,2.,15.,15.]}}])
    monkeypatch.setattr(foundation,'feature_encoder',lambda *args:Encoder())
    monkeypatch.setattr(foundation,'extract_features',lambda *args:torch.tensor([[1.,0.]]))
    candidate=providers.foundation_candidates(image,{},prompt='part',positive_examples=[{
        'image_path':str(image),'roi':[2,2,15,15]}])[0]
    assert candidate['provenance']['prompt']=='part'
    assert candidate['provenance']['examples'][0]['kind']=='positive'
    assert candidate['confidence']==pytest.approx(.8)
    monkeypatch.setattr(providers,'grounded_candidates',lambda *args,**kwargs:[])
    assert providers.foundation_candidates(image,{},prompt='missing part',
        points=[{'x':8.,'y':4.,'label':1}])==[]
