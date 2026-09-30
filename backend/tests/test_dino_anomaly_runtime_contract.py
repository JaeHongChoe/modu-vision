"""Native anomaly maps carry patch-score semantics, never segmentation truth."""
import numpy as np
import pytest
import torch
from backend.engine.anomaly.evaluation import evaluate_anomaly_dataset
from backend.engine.flowchart_engine import FlowchartEngine, FlowNode, FlowNodeData


class PatchScoreModel:
    model_metadata = {'map_semantics': 'patch_score', 'detector_type': 'dino_synthetic'}
    threshold = .8
    def __call__(self, image):
        return torch.full((1, *image.shape[-2:]), .9), torch.tensor([.9])
    def predict_anomaly_map(self, image, out_size=None):
        self.shape = tuple(image.shape[-2:])
        return np.full(self.shape, .9, dtype=np.float32), .9


def test_patch_scores_do_not_claim_pixel_segmentation_metrics():
    class Data:
        samples = [('normal', 0, None), ('defect', 1, 'true-mask')]
        def __len__(self): return 2
        def __getitem__(self, index):
            return torch.zeros(3, 32, 48), index, torch.full((32, 48), index)
    metrics = evaluate_anomaly_dataset(PatchScoreModel(), Data())
    assert metrics['pixel_evaluated_images'] == 0
    assert metrics['pixel_auroc'] is None
    assert metrics['map_semantics'] == 'patch_score'


def test_final_validation_keeps_native_patch_image_on_cpu():
    class Data:
        samples = [('normal',0,None)]
        def __len__(self): return 1
        def __getitem__(self,index): return torch.zeros(3,80,96),0,None
    class NativeModel(PatchScoreModel):
        def __call__(self,image):
            assert image.device.type == 'cpu' and image.shape[-2:] == (80,96)
            return super().__call__(image)
    result = evaluate_anomaly_dataset(NativeModel(),Data(),device='cuda')
    assert result['sample_count'] == 1


def test_flow_preserves_native_patch_input_and_does_not_fabricate_mask(monkeypatch):
    engine = FlowchartEngine(device='cpu')
    model = PatchScoreModel()
    monkeypatch.setattr(engine, '_get_inspection_model', lambda **kw: (model, True))
    monkeypatch.setattr(engine, '_resolve_checkpoint', lambda *args: None)
    node = FlowNode(id='anomaly', position={'x':0,'y':0}, data=FlowNodeData(label='anomaly', node_type='inspection', task='anomaly', threshold=.8))
    results, _, _ = engine._inspect_crops(np.zeros((80, 96, 3), dtype=np.uint8),
        [{'id':'full_image','bbox':[0,0,96,80],'label':'image'}], node)
    assert model.shape == (80, 96)
    assert results[0].anomaly_values['shape'] == [80, 96]
    assert results[0].map_semantics == 'patch_score'
    assert results[0].mask is None
    assert results[0].defect_area_px is None


def test_patch_score_model_cannot_satisfy_segmentation_mode(monkeypatch):
    engine = FlowchartEngine(device='cpu')
    monkeypatch.setattr(engine, '_get_inspection_model', lambda **kw: (PatchScoreModel(), True))
    monkeypatch.setattr(engine, '_resolve_checkpoint', lambda *args: None)
    node = FlowNode(id='anomaly', position={'x':0,'y':0}, data=FlowNodeData(label='anomaly', node_type='inspection', task='anomaly', params={'anomaly_mode':'segmentation'}))
    with pytest.raises(RuntimeError, match='patch|Patch'):
        engine._inspect_crops(np.zeros((80, 96, 3), dtype=np.uint8),
            [{'id':'full_image','bbox':[0,0,96,80],'label':'image'}], node)


def test_real_evaluation_uses_native_scale_and_frozen_threshold(tmp_path, monkeypatch):
    from PIL import Image
    from backend.api import routes_evaluation as routes
    dataset = tmp_path / 'data' / 'test'
    for label in ('good', 'scratch'):
        directory = dataset / label
        directory.mkdir(parents=True)
        Image.new('RGB', (96, 80), 'white').save(directory / (label + '.png'))
    model = PatchScoreModel()
    monkeypatch.setattr(routes, 'reconstruct_anomaly_detector', lambda *args: model)
    checkpoint = tmp_path / 'best_model.pt'
    torch.save({'model_state_dict': {}}, checkpoint)
    result = routes._evaluate_anomaly(checkpoint,
        {'detector_type':'dino_synthetic', 'map_semantics':'patch_score', 'image_size':[32,32]},
        dataset.parent, torch.device('cpu'))
    assert result['metrics']['optimal_threshold'] == .8
    assert result['metrics']['pixel_evaluated_images'] == 0
    assert result['metrics']['pixel_auroc'] is None
    item = result['test_predictions'][0]
    assert item['map_semantics'] == 'patch_score'
    with np.load(item['pixel_evidence']['file_path'], allow_pickle=False) as archive:
        assert archive[item['pixel_evidence']['heatmap_key']].shape == (80, 96)


def test_empirical_normal_threshold_keeps_equal_score_normal():
    from backend.engine.anomaly.metrics import compute_anomaly_metrics
    metrics = compute_anomaly_metrics([.8,.9],[0,1],fixed_threshold=.8,threshold_comparison='gt')
    assert metrics['confusion_matrix'] == [[1,0],[0,1]]
    assert metrics['f1_score'] == 1.


def test_flow_patch_map_uses_lossless_compact_payload(monkeypatch):
    import base64
    import zlib
    engine = FlowchartEngine(device='cpu')
    monkeypatch.setattr(engine, '_get_inspection_model', lambda **kw: (PatchScoreModel(), True))
    monkeypatch.setattr(engine, '_resolve_checkpoint', lambda *args: None)
    node = FlowNode(id='anomaly',position={'x':0,'y':0},data=FlowNodeData(label='anomaly',node_type='inspection',task='anomaly'))
    rows,_,_=engine._inspect_crops(np.zeros((512,512,3),dtype=np.uint8),
        [{'id':'full_image','bbox':[0,0,512,512],'label':'image'}],node)
    encoded = rows[0].anomaly_values
    assert encoded['encoding'] == 'zlib_base64'
    assert len(encoded['data']) < 10000
    restored = np.frombuffer(zlib.decompress(base64.b64decode(encoded['data'])),dtype='<f4').reshape(encoded['shape'])
    assert restored.shape == (512,512)
    assert restored[511,511] == pytest.approx(.9)
