"""Local foundation masks and learned region features, with explicit receipts.

Model acquisition is separate from inference. No missing model is replaced by
thresholding, template matching or a random feature encoder.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import math
import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

_LOCK = threading.RLock()
_SAM_CACHE = {}
_FEATURE_CACHE = {}


class LabelingCancelled(RuntimeError):
    pass


def check_cancel(cancel=None):
    if cancel is not None and cancel.is_set():
        raise LabelingCancelled('Labeling cancelled')


def resolve_device(device='auto'):
    import torch
    if device == 'auto':
        device = 'cuda:0' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
    if device == 'cpu':
        return device
    if device == 'mps' and torch.backends.mps.is_available():
        return device
    if device.startswith('cuda:') and device[5:].isdigit() and torch.cuda.is_available() and int(device[5:]) < torch.cuda.device_count():
        return device
    raise ValueError(f'Device {device} is unavailable. Select CPU or an available GPU.')


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_rgb(path, expected_sha256=None):
    data = Path(path).read_bytes()
    if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError('Source image changed during labeling')
    from backend.engine.dicom_input import is_dicom, read_dicom
    source_image = read_dicom(path)[0] if is_dicom(path) else Image.open(io.BytesIO(data))
    with source_image as image:
        if image.width * image.height > 128_000_000:
            raise ValueError('Image exceeds the 128 megapixel labeling resource guard; use an inspection ROI')
        return image.convert('RGB')


def foundation_readiness(setup, device='cpu'):
    directory = setup.get('mask_model_dir')
    error = None
    dependency = importlib.util.find_spec('transformers') is not None
    if not dependency:
        error = 'SAM2 requires the optional transformers>=4.57.6 labeling dependencies in this backend environment.'
    elif not directory or not Path(directory).is_dir():
        error = 'Configure mask_model_dir with a local SAM2 model (for example facebook/sam2.1-hiera-tiny).'
    else:
        try:
            root = Path(directory)
            if json.loads((root / 'config.json').read_text()).get('model_type') not in ('sam2','sam2_video') or not (root / 'model.safetensors').is_file():
                error = 'SAM2 config.json and model.safetensors are required; download official weights before use.'
        except (OSError, ValueError):
            error = 'SAM2 model configuration is missing or invalid.'
    try:
        selected = resolve_device(device)
    except ValueError as exc:
        selected = device
        error = str(exc)
    return {'backend': 'foundation', 'ready': error is None, 'device': selected,
            'dependency_available': dependency, 'mask_model_dir': directory, 'error': error,
            'capabilities': ['point_mask', 'box_mask', 'polygon', 'mask', 'text_with_grounding_dino',
                             'positive_negative_with_dinov3', 'batch', 'cancellation', 'few_label_classifier'],
            'limits': 'SAM2 IoU predictions and DINO cosine scores are not calibrated class probabilities. Review every proposed label.'}


class _Sam2Adapter:
    def __init__(self, directory, device):
        from transformers import Sam2Model, Sam2Processor, Sam2Config
        from backend.engine.label_candidate_providers import model_directory_hash
        self.processor = Sam2Processor.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
        # The official repository also distributes the video superset. Use its
        # image encoder/prompt decoder configuration and require every image
        # model parameter to have loaded; unused video-memory keys are benign.
        config=Sam2Config.from_dict(json.loads((Path(directory)/'config.json').read_text()))
        self.model,loading=Sam2Model.from_pretrained(directory,config=config,local_files_only=True,
            trust_remote_code=False,output_loading_info=True)
        if loading['missing_keys'] or loading['mismatched_keys']:
            raise ValueError('SAM2 checkpoint lacks complete image segmentation weights; no random parameters are permitted')
        self.model=self.model.to(device).eval()
        self.device = device
        self.provenance = {'provider': 'sam2', 'model_dir': str(directory), 'model_sha256': model_directory_hash(directory), 'device': device}
        self._image = None
        self._embeddings = None

    def predict(self, rgb, points, box, cancel=None):
        import torch
        check_cancel(cancel)
        prompts = {}
        if points:
            prompts['input_points'] = [[[[p['x'], p['y']] for p in points]]]
            prompts['input_labels'] = [[[p['label'] for p in points]]]
        if box:
            prompts['input_boxes'] = [[box]]
        inputs = self.processor(images=rgb, return_tensors='pt', **prompts).to(self.device)
        with torch.inference_mode():
            if self._image is not rgb:
                self._embeddings = self.model.get_image_embeddings(inputs['pixel_values'])
                self._image = rgb
            check_cancel(cancel)
            inputs.pop('pixel_values')
            outputs = self.model(**inputs, image_embeddings=self._embeddings, multimask_output=True)
            masks = self.processor.post_process_masks(outputs.pred_masks.cpu(), inputs['original_sizes'].cpu())[0]
        check_cancel(cancel)
        scores = outputs.iou_scores.detach().cpu().reshape(-1)
        index = int(scores.argmax())
        mask = masks.reshape(-1, rgb.height, rgb.width)[index].numpy() > 0
        return mask, float(scores[index].clamp(0, 1))


def _load_sam2(setup, device):
    readiness = foundation_readiness(setup, device)
    if not readiness['ready']:
        raise ValueError(readiness['error'])
    from backend.engine.label_candidate_providers import model_directory_hash
    directory = str(Path(setup['mask_model_dir']).expanduser().resolve())
    signature = model_directory_hash(directory)
    key = (directory, signature, device)
    if key not in _SAM_CACHE:
        _SAM_CACHE.clear()
        _SAM_CACHE[key] = _Sam2Adapter(directory, device)
    return _SAM_CACHE[key]


def prompt_chunks(prompt, max_words=60):
    """Preserve all non-whitespace text while bounding each grounding call."""
    words = prompt.split()
    if max_words < 1:
        raise ValueError('Prompt chunk size must be positive')
    # Character chunks handle a single unbroken long token as well.
    words = [piece for word in words for piece in (word[i:i+120] for i in range(0, len(word), 120))]
    return [' '.join(words[i:i + max_words]) for i in range(0, len(words), max_words)]


def _box(box, width, height):
    if len(box) != 4 or not all(math.isfinite(float(v)) for v in box):
        raise ValueError('Box needs four finite coordinates')
    x1, y1, x2, y2 = map(float, box)
    if not 0 <= x1 < x2 <= width or not 0 <= y1 < y2 <= height:
        raise ValueError('Prompt box is outside the source image or has zero area')
    return [x1, y1, x2, y2]


def visual_scores(features, positives, negatives):
    def normalize(x):
        x = np.asarray(x, dtype=np.float32)
        return x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
    features = normalize(features)
    positive = np.max(features @ normalize(positives).T, axis=1) if len(positives) else np.ones(len(features))
    negative = np.max(features @ normalize(negatives).T, axis=1) if len(negatives) else np.full(len(features), -1.)
    return np.clip((positive + 1) / 2, 0, 1), positive > negative


def feature_encoder(setup, device):
    from backend.engine.model_backbones import DinoTaskModel, DINO_MODELS, canonical_dino_name
    name = canonical_dino_name(setup.get('feature_backbone', 'dinov3_vits16'))
    checkpoint = setup.get('feature_checkpoint')
    if not checkpoint:
        try:
            from huggingface_hub import hf_hub_download
            checkpoint = hf_hub_download(f'timm/{DINO_MODELS[name]}', 'model.safetensors', local_files_only=True)
        except Exception as exc:
            raise ValueError('Configure feature_checkpoint with authentic local DINOv3 weights for image examples or few-label training.') from exc
    digest = file_sha256(checkpoint)
    if setup.get('feature_sha256') and setup['feature_sha256'] != digest:
        raise ValueError('DINOv3 feature checkpoint SHA256 mismatch')
    key = (name, digest, device)
    if key not in _FEATURE_CACHE:
        model = DinoTaskModel('classification', name, 1, pretrained=True, pretrained_checkpoint=checkpoint, pretrained_sha256=digest).to(device).eval()
        model.model_metadata['feature_checkpoint']=str(checkpoint)
        _FEATURE_CACHE.clear(); _FEATURE_CACHE[key] = model
    return _FEATURE_CACHE[key]


def extract_features(images, setup, device='cpu', cancel=None):
    import torch
    model = feature_encoder(setup, device)
    results = []
    for image in images:
        check_cancel(cancel)
        resized = image.convert('RGB').resize((224, 224), Image.Resampling.BILINEAR)
        tensor = torch.from_numpy(np.asarray(resized).copy()).permute(2, 0, 1).float().unsqueeze(0).to(device) / 255
        with torch.inference_mode():
            tokens = model.encoder.forward_features((tensor - model.input_mean) / model.input_std)
            results.append(tokens[:, 0].detach().cpu())
    check_cancel(cancel)
    return torch.cat(results).float() if results else torch.empty((0, model.encoder.num_features))


def mask_candidate(mask, score, label, output_geometry, provenance):
    mask = np.asarray(mask, bool)
    ys, xs = np.where(mask)
    if not len(xs):
        return None
    bbox = [float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)]
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea)
    simplified = cv2.approxPolyDP(contour, max(.5, cv2.arcLength(contour, True) * .002), True)
    polygon = [[float(x), float(y)] for x, y in simplified.reshape(-1, 2)]
    if len(polygon) < 3:
        return None
    rgba = np.zeros((*mask.shape, 4), np.uint8); rgba[..., :3] = [34, 211, 238]; rgba[..., 3] = mask * 255
    stream = io.BytesIO(); Image.fromarray(rgba).save(stream, format='PNG')
    encoded = 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode('ascii')
    annotation = {'type': 'brush_mask' if output_geometry == 'mask' else output_geometry,
                  'label': label, 'category_id': 1, 'bbox': bbox, 'color': '#22d3ee'}
    if output_geometry == 'polygon': annotation['polygon'] = polygon
    if output_geometry == 'mask': annotation['mask_rle'] = encoded
    return {'confidence': float(score), 'annotation': annotation, 'polygon': polygon, 'mask_rle': encoded,
            'area': int(mask.sum()), 'source': 'sam2', 'provenance': dict(provenance)}


def foundation_candidates(image_path, setup, *, prompt='', label='defect', points=None, boxes=None,
                          positive_examples=None, negative_examples=None, device='cpu', threshold=.5,
                          text_threshold=.25, min_area=0, max_area=None, min_width=0, max_width=None,
                          min_height=0, max_height=None, max_candidates=20, output_geometry='polygon',
                          cancel=None, suggestion_model=None):
    check_cancel(cancel)
    device = resolve_device(device)
    rgb = read_rgb(image_path)
    points = list(points or [])
    for point in points:
        if (point.get('label') not in (0, 1) or not all(math.isfinite(float(point[k])) for k in ('x', 'y'))
                or not 0 <= point['x'] < rgb.width or not 0 <= point['y'] < rgb.height):
            raise ValueError('Prompt point is outside the source image or has an invalid label')
    box_prompts = [_box(box, rgb.width, rgb.height) for box in boxes or []]
    if output_geometry not in ('polygon', 'mask', 'bbox'):
        raise ValueError('Unknown output geometry')
    examples = []
    for kind, group in [('positive', positive_examples or []), ('negative', negative_examples or [])]:
        for example in group:
            image = read_rgb(example['image_path'], example.get('sha256'))
            roi = _box(example['roi'], image.width, image.height)
            examples.append((kind, image.crop(tuple(roi)).resize((224,224),Image.Resampling.BILINEAR), {'kind': kind, 'image_path': str(example['image_path']), 'roi': roi, 'sha256': file_sha256(example['image_path'])}))
    if not (prompt.strip() or points or box_prompts or examples or suggestion_model):
        raise ValueError('Provide text, an image example, a point or a box prompt')
    with _LOCK:
        model = _load_sam2(setup, device)
        grounding = []
        grounding_scores={}
        grounding_receipt={}
        if prompt.strip():
            from backend.engine.label_candidate_providers import grounded_candidates,semantic_readiness,model_directory_hash
            directory=setup.get('model_dir')
            if directory and semantic_readiness(directory)['ready']:
                grounding_receipt={'model_dir':directory,'model_sha256':model_directory_hash(directory)}
            grounding = grounded_candidates(image_path, setup.get('model_dir'), prompt, threshold, text_threshold, device=device, cancel=cancel)
            manual_boxes=box_prompts
            box_prompts=[]
            for item in grounding:
                grounded_box=item['annotation']['bbox']
                for selected in manual_boxes or [grounded_box]:
                    box=[max(selected[0],grounded_box[0]),max(selected[1],grounded_box[1]),min(selected[2],grounded_box[2]),min(selected[3],grounded_box[3])]
                    if box[0]>=box[2] or box[1]>=box[3]:continue
                    if points and any(p['label']==1 and not(box[0]<=p['x']<=box[2] and box[1]<=p['y']<=box[3]) for p in points):continue
                    box_prompts.append(box);grounding_scores[tuple(box)]=float(item['confidence'])
        # Image-example searches and trained classifiers use SAM2 grid prompts,
        # never a synthetic region generator. Every output is a model mask.
        prompts = [(points, box) for box in box_prompts] if box_prompts else [(points, None)] if points and not prompt.strip() else []
        if (examples or suggestion_model) and not prompts and not prompt.strip():
            prompts = [([{'x': (x + .5) * rgb.width / 8, 'y': (y + .5) * rgb.height / 8, 'label': 1}], None) for y in range(8) for x in range(8)]
        candidates = []; masks = []
        bit_counts=np.array([int(i).bit_count() for i in range(256)],dtype=np.uint8)
        provenance = {**model.provenance, 'device': device, 'prompt': prompt,'grounding':grounding_receipt,
                      'examples': [e[2] for e in examples], 'score_kind': 'predicted_mask_iou'}
        for index, (mask_points, box) in enumerate(prompts):
            check_cancel(cancel)
            mask, score = model.predict(rgb, mask_points, box, cancel)
            if box is not None and tuple(box) in grounding_scores: score=min(score,grounding_scores[tuple(box)])
            if mask.shape != (rgb.height, rgb.width): raise ValueError('SAM2 mask dimensions do not match source image')
            if score < threshold: continue
            candidate = mask_candidate(mask, score, label, output_geometry, provenance)
            if candidate is None: continue
            if prompt.strip(): candidate['provenance']['score_kind']='minimum_mask_iou_and_grounding_score'
            x1, y1, x2, y2 = candidate['annotation']['bbox']; area = candidate['area']
            if area < min_area or max_area is not None and area > max_area: continue
            if x2-x1 < min_width or max_width is not None and x2-x1 > max_width: continue
            if y2-y1 < min_height or max_height is not None and y2-y1 > max_height: continue
            packed=np.packbits(mask)
            if any(int(bit_counts[np.bitwise_and(packed,old)].sum()) / max(1,int(bit_counts[np.bitwise_or(packed,old)].sum())) > .8 for old in masks): continue
            masks.append(packed); candidates.append(candidate)
        if (examples or suggestion_model) and candidates:
            crops = [rgb.crop(tuple(item['annotation']['bbox'])).resize((224,224),Image.Resampling.BILINEAR) for item in candidates]
            features = extract_features(crops, setup, device, cancel)
            encoder = feature_encoder(setup, device)
            if suggestion_model and (suggestion_model['feature_metadata']['pretrained_sha256']!=encoder.model_metadata['pretrained_sha256']
                    or suggestion_model['feature_metadata']['backbone']!=encoder.model_metadata['backbone']):
                raise ValueError('Trained classifier feature architecture or checkpoint differs from the selected foundation feature provider')
            for candidate in candidates: candidate['provenance']['feature_metadata'] = dict(encoder.model_metadata)
            candidate_features = {id(candidate): feature for candidate, feature in zip(candidates, features)}
            if examples:
                example_features = extract_features([e[1] for e in examples], setup, device, cancel).numpy()
                positives = example_features[[i for i, e in enumerate(examples) if e[0] == 'positive']]
                negatives = example_features[[i for i, e in enumerate(examples) if e[0] == 'negative']]
                scores, keep = visual_scores(features.numpy(), positives, negatives)
                selected = []
                for candidate, similarity, accepted in zip(candidates, scores, keep):
                    candidate['provenance'].update(score_kind='minimum_mask_iou_and_cosine_similarity', visual_similarity=float(similarity))
                    candidate['confidence'] = min(candidate['confidence'], float(similarity))
                    if accepted and candidate['confidence'] >= threshold: selected.append(candidate)
                candidates = selected
            if suggestion_model:
                import torch
                state = suggestion_model['state']; classes = suggestion_model['classes']
                selected_features = torch.stack([candidate_features[id(candidate)] for candidate in candidates]) if candidates else features[:0]
                probabilities = (selected_features @ state['weight'].T + state['bias']).softmax(1)
                selected = []
                for candidate, probability in zip(candidates, probabilities):
                    category = int(probability.argmax()); confidence = float(probability[category])
                    if classes[category] == '__background__' or confidence < threshold: continue
                    candidate['annotation'].update(label=classes[category], category_id=max(1,category if classes[0]=='__background__' else category+1))
                    candidate['confidence'] = min(candidate['confidence'], confidence)
                    candidate['provenance'].update(score_kind='minimum_mask_iou_and_learned_class_probability', suggestion_model_id=suggestion_model['id'])
                    selected.append(candidate)
                candidates = selected
        check_cancel(cancel)
        return sorted(candidates, key=lambda c: c['confidence'], reverse=True)[:max_candidates]


def fit_feature_classifier(features, targets, num_classes, *, epochs=100, learning_rate=.05, parent_state=None, cancel=None):
    import torch
    check_cancel(cancel)
    if features.ndim != 2 or len(features) != len(targets) or not len(features) or num_classes < 2:
        raise ValueError('Feature fitting needs labeled regions in at least two classes')
    if not torch.isfinite(features).all() or not 1 <= epochs <= 1000 or not 0 < learning_rate <= 1:
        raise ValueError('Invalid feature training data or training parameters')
    head = torch.nn.Linear(features.shape[1], num_classes)
    torch.nn.init.zeros_(head.weight);torch.nn.init.zeros_(head.bias)
    if parent_state: head.load_state_dict({k: v.clone() for k, v in parent_state.items()}, strict=True)
    optimizer = torch.optim.Adam(head.parameters(), lr=learning_rate)
    history = []
    for _ in range(epochs):
        check_cancel(cancel)
        optimizer.zero_grad()
        loss = torch.nn.functional.cross_entropy(head(features), targets)
        if not torch.isfinite(loss): raise ValueError('Nonfinite feature-classifier loss')
        loss.backward(); optimizer.step(); history.append(float(loss.detach()))
    check_cancel(cancel)
    return {key: value.detach().cpu().clone() for key, value in head.state_dict().items()}, history
