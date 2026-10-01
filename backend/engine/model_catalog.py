"""Public model capabilities; no models, data or credentials are loaded here."""
from __future__ import annotations

from importlib.util import find_spec


def model_family_catalog():
    from backend.engine.automated_trials import _RUNNERS
    definitions = [
        ("classification", "이미지 분류", "DINOv3", ["dinov3_vits16", "dinov3_vitb16", "resnet18", "convnext_tiny", "efficientnet_b0"], "dinov3_vits16", "클래스별 이미지와 서로 분리된 train/val/test", True, "weight_initialization", ["timm"]),
        ("segmentation", "영역 분할", "DINOv3", ["dinov3_vits16", "dinov3_vitb16", "unet"], "dinov3_vits16", "픽셀 마스크 또는 LabelMe polygon", True, "weight_initialization", ["timm"]),
        ("detection", "객체 검출", "YOLO", ["yolo26n", "yolo26s", "fasterrcnn"], "yolo26n", "객체 bbox 또는 LabelMe·COCO 검출 라벨", True, "weight_initialization", ["ultralytics"]),
        ("anomaly", "이상탐지", "PaDiM / PatchCore / DINOv3 합성 결함", ["padim", "patchcore", "dino_synthetic"], "padim", "정상 학습 이미지와 독립 시험 데이터; 영역 평가는 정답 마스크 필요", True, "statistical_refit", []),
        ("patch_classification", "패치 분류", "DINOv3", ["dinov3_vits16", "dinov3_vitb16", "resnet18", "convnext_tiny", "efficientnet_b0"], "dinov3_vits16", "패치 좌표·클래스·원본 해시 manifest", False, "weight_initialization", ["timm"]),
        ("ocr", "문자 인식", "CTC 문자 인식", ["ctc"], "ctc", "문자 이미지와 사람이 확인한 실제 정답 문자열", False, "weight_initialization", []),
        ("rotated_detection", "회전 객체 검출", "회전 박스 검출", ["rotated_detector"], "rotated_detector", "클래스·회전 박스 또는 polygon", False, "weight_initialization", []),
        ("defect_gan", "결함 이미지 생성", "GAN", ["defect_gan"], "defect_gan", "실제 결함 crop; 생성 결과는 검토 후 train에만 채택", False, "weight_initialization", []),
        ("enhancement", "이미지 개선", "입력·정답 쌍 학습", ["enhancement"], "enhancement", "개선 전 입력과 개선 정답 이미지 쌍", False, "weight_initialization", []),
        ("rotation", "정방향 보정", "학습형 각도 예측", ["small_cnn_angle_v1"], "small_cnn_angle_v1", "이미지와 사람이 확인한 정방향 보정각·독립 train/val/test", False, "weight_initialization", []),
    ]
    families = []
    for task, label, model, architectures, default, prerequisite, remote, continuation, dependencies in definitions:
        missing = [name for name in dependencies if find_spec(name) is None]
        families.append({
            "task": task, "label": label, "model": model,
            "architectures": architectures, "default_architecture": default,
            "prerequisite": prerequisite, "remote_training": task in _RUNNERS,
            "continuation": continuation,
            "devices": ["cpu", "cuda", "mps"],
            "stages": ["label", "train", "evaluate", "generate", "review", "export"] if task == "defect_gan" else ["label", "train", "evaluate", "flow", "export"],
            "dependencies": dependencies, "missing_dependencies": missing,
            "quality_approved": False,
            "automated_training": True,
        })
        if task == 'anomaly':
            families[-1]['methods'] = [
                {'method': kind, 'architectures': [kind], 'continuation': 'statistical_refit',
                 'prerequisite': '정상 학습 이미지로 특징 통계를 구성합니다.', 'dependencies': [], 'missing_dependencies': []}
                for kind in ('padim', 'patchcore')
            ] + [{'method': 'dino_synthetic', 'architectures': ['dinov3_vits16', 'dinov3_vitb16', 'dinov3_vitl16'],
                  'continuation': 'weight_initialization', 'map_semantics': 'patch_score',
                  'prerequisite': '정상 원본에서 합성 결함 패치를 학습합니다. 실제 NG는 평가에만 사용합니다. 점수 맵은 픽셀 정답 마스크가 아닙니다.',
                  'dependencies': ['timm', 'safetensors', 'huggingface_hub'],
                  'missing_dependencies': [name for name in ('timm', 'safetensors', 'huggingface_hub') if find_spec(name) is None]}]
    return {"families": families, "schema_version": 1}
