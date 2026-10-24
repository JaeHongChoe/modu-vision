"""The adapter boundary is controlled; geometry, filtering and fitting are real."""
import base64
import io
import threading

import numpy as np
import pytest
from PIL import Image

from backend.engine import label_candidate_providers as providers


@pytest.fixture
def sam2_counting_adapter(tmp_path, monkeypatch):
    """Run the real adapter against a deterministic processor/encoder/decoder, without weights."""
    import copy
    import json
    import sys
    from types import SimpleNamespace
    import torch
    from backend.engine import foundation_labeling as foundation

    class Batch(dict):
        def to(self, device):
            assert device == 'cpu'
            return self

    class Processor:
        fail_postprocess = False

        def __call__(self, *, images, return_tensors, **prompts):
            assert return_tensors == 'pt'
            pixels = np.asarray(images).copy()
            if pixels.ndim == 2:
                pixels = pixels[..., None]
            pixels = torch.from_numpy(pixels).permute(2, 0, 1).unsqueeze(0).float()
            return Batch(pixel_values=pixels, original_sizes=torch.tensor([[images.height, images.width]]),
                         expected_pixels=pixels.clone(), **prompts)

        def post_process_masks(self, masks, sizes):
            if self.fail_postprocess:
                self.fail_postprocess = False
                raise RuntimeError('controlled postprocess failure')
            assert tuple(masks.shape[-2:]) == tuple(sizes[0].tolist())
            return [masks]

    class Model:
        def __init__(self):
            self.encodes = 0
            self.decodes = []
            self.fail_encode = False
            self.fail_decode = False
            self.after_encode = lambda: None
            self.after_decode = lambda: None

        def to(self, device):
            assert device == 'cpu'
            return self

        def eval(self):
            return self

        def get_image_embeddings(self, pixels):
            self.encodes += 1
            if self.fail_encode:
                self.fail_encode = False
                raise RuntimeError('controlled encode failure')
            self.after_encode()
            return pixels.clone()

        def __call__(self, *, image_embeddings, expected_pixels, original_sizes, multimask_output, **prompts):
            # The exact current request must decode against its own pixels, including after mutations/contenders.
            assert torch.equal(image_embeddings, expected_pixels), 'Embedding belongs to different decoded pixels'
            assert multimask_output is True
            self.decodes.append(copy.deepcopy(prompts))
            if self.fail_decode:
                self.fail_decode = False
                raise RuntimeError('controlled decode failure')
            self.after_decode()
            return SimpleNamespace(pred_masks=image_embeddings[:, :1] > 0, iou_scores=torch.tensor([[.91]]))

    processor, model = Processor(), Model()
    module = SimpleNamespace(
        Sam2Processor=SimpleNamespace(from_pretrained=lambda *a, **k: processor),
        Sam2Model=SimpleNamespace(from_pretrained=lambda *a, **k: (model, {'missing_keys': [], 'mismatched_keys': []})),
        Sam2Config=SimpleNamespace(from_dict=lambda value: value),
    )
    monkeypatch.setitem(sys.modules, 'transformers', module)
    monkeypatch.setattr(providers, 'model_directory_hash', lambda path: 'controlled-model-signature')
    directory = tmp_path / 'controlled-model'
    directory.mkdir()
    (directory / 'config.json').write_text(json.dumps({'model_type': 'sam2'}))
    adapter = foundation._Sam2Adapter(str(directory), 'cpu')
    return adapter, model, processor


def test_sam2_embedding_reuses_fresh_identical_images_but_decodes_every_prompt(sam2_counting_adapter):
    adapter, model, _ = sam2_counting_adapter
    first = [{'x': 2, 'y': 2, 'label': 1}]
    second = [*first, {'x': 3, 'y': 3, 'label': 0}]
    a = adapter.predict(Image.new('RGB', (8, 6), 'white'), first, None)
    b = adapter.predict(Image.new('RGB', (8, 6), 'white'), second, [1, 1, 7, 5])
    assert model.encodes == 1
    assert len(model.decodes) == 2
    assert model.decodes[0]['input_labels'] == [[[1]]]
    assert model.decodes[1]['input_labels'] == [[[1, 0]]]
    assert model.decodes[1]['input_boxes'] == [[[1, 1, 7, 5]]]
    np.testing.assert_array_equal(a[0], b[0])
    assert a[1] == b[1]


def test_sam2_embedding_in_place_mutation_invalidates_same_object(sam2_counting_adapter):
    adapter, model, _ = sam2_counting_adapter
    image = Image.new('RGB', (8, 6), 'white')
    adapter.predict(image, [], None)
    image.putpixel((2, 2), (0, 0, 0))
    mask, _ = adapter.predict(image, [], None)
    assert model.encodes == 2
    assert not mask[2, 2]


def test_sam2_embedding_key_distinguishes_pixels_dimensions_and_mode_and_is_single_entry(sam2_counting_adapter):
    adapter, model, _ = sam2_counting_adapter
    # RGB 2x6 and RGB 3x4 have identical byte strings; mode RGB 2x2 and L 2x6 do too.
    # L and P also have identical dimensions/bytes, isolating mode from the other key parts.
    images = [Image.new('RGB', (2, 6), 0), Image.new('RGB', (3, 4), 0),
              Image.new('RGB', (2, 2), 0), Image.new('L', (2, 6), 0),
              Image.new('P', (2, 6), 0), Image.new('L', (2, 6), 1)]
    assert images[3].size == images[4].size and images[3].tobytes() == images[4].tobytes()
    for index, image in enumerate(images):
        adapter.predict(image, [], None)
        assert model.encodes == index + 1
    adapter.predict(images[0].copy(), [], None)
    assert model.encodes == len(images) + 1, 'Only the most recent image embedding is retained'


@pytest.mark.parametrize('stage', ['encode', 'decode', 'postprocess'])
def test_sam2_embedding_failure_keeps_previous_complete_entry(sam2_counting_adapter, stage):
    adapter, model, processor = sam2_counting_adapter
    adapter.predict(Image.new('RGB', (8, 6), 'white'), [], None)
    if stage == 'postprocess':
        processor.fail_postprocess = True
    else:
        setattr(model, 'fail_' + stage, True)
    with pytest.raises(RuntimeError, match='controlled .* failure'):
        adapter.predict(Image.new('RGB', (8, 6), 'black'), [], None)
    assert model.encodes == 2
    adapter.predict(Image.new('RGB', (8, 6), 'white'), [], None)
    assert model.encodes == 2, 'A partial replacement must not evict the completed image'


@pytest.mark.parametrize('stage', ['before', 'after_encode', 'after_decode'])
def test_sam2_embedding_cancellation_keeps_previous_complete_entry(sam2_counting_adapter, stage):
    from backend.engine import foundation_labeling as foundation
    adapter, model, _ = sam2_counting_adapter
    adapter.predict(Image.new('RGB', (8, 6), 'white'), [], None)
    cancel = threading.Event()
    if stage == 'before':
        cancel.set()
    else:
        setattr(model, stage, cancel.set)
    with pytest.raises(foundation.LabelingCancelled):
        adapter.predict(Image.new('RGB', (8, 6), 'black'), [], None, cancel)
    encodes_after_cancel = model.encodes
    model.after_encode = lambda: None
    model.after_decode = lambda: None
    adapter.predict(Image.new('RGB', (8, 6), 'white'), [], None)
    assert model.encodes == encodes_after_cancel


def test_sam2_embedding_public_requests_are_serialized(sam2_counting_adapter, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from backend.engine import foundation_labeling as foundation
    adapter, model, _ = sam2_counting_adapter
    first = tmp_path / 'first.png'
    second = tmp_path / 'second.png'
    Image.new('RGB', (8, 6), 'white').save(first)
    Image.new('RGB', (8, 6), 'gray').save(second)
    monkeypatch.setattr(foundation, '_load_sam2', lambda setup, device: adapter)
    entered, release, second_loaded = threading.Event(), threading.Event(), threading.Event()
    read = foundation.read_rgb

    def observed_read(path, *args):
        image = read(path, *args)
        if path == second:
            second_loaded.set()
        return image

    monkeypatch.setattr(foundation, 'read_rgb', observed_read)

    def hold_first_encode():
        if model.encodes == 1:
            entered.set()
            assert release.wait(5), 'Controlled encode hold exceeded its deadline'

    model.after_encode = hold_first_encode
    def predict(path):
        return foundation.foundation_candidates(path, {}, points=[{'x': 2, 'y': 2, 'label': 1}], output_geometry='mask')

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(predict, first)
        try:
            assert entered.wait(5)
            b = pool.submit(predict, second)
            assert second_loaded.wait(5), 'Second request must reach the original engine serialization boundary'
            assert model.encodes == 1 and model.decodes == []
        finally:
            release.set()
        assert len(a.result(timeout=5)) == 1
        assert len(b.result(timeout=5)) == 1
    assert model.encodes == 2 and len(model.decodes) == 2


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
