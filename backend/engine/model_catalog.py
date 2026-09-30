"""Public model capabilities; no models, data or credentials are loaded here."""
from __future__ import annotations

from importlib.util import find_spec


def model_family_catalog():
    definitions = [
        ("classification", "이미지 분류", "DINOv3", ["dinov3_vits16", "dinov3_vitb16", "resnet18", "convnext_tiny", "efficientnet_b0"], "dinov3_vits16", "클래스별 이미지와 서로 분리된 train/val/test", True, "weight_initialization", ["timm"]),
        ("segmentation", "영역 분할", "DINOv3", ["dinov3_vits16", "dinov3_vitb16", "unet"], "dinov3_vits16", "픽셀 마스크 또는 LabelMe polygon", True, "weight_initialization", ["timm"]),
        ("detection", "객체 검출", "YOLO", ["yolo26n", "yolo26s", "fasterrcnn"], "yolo26n", "객체 bbox 또는 LabelMe·COCO 검출 라벨", True, "weight_initialization", ["ultralytics"]),
        ("anomaly", "이상탐지", "PaDiM / PatchCore", ["padim", "patchcore"], "padim", "정상 학습 이미지와 독립 시험 데이터; 영역 평가는 정답 마스크 필요", True, "statistical_refit", []),
        ("patch_classification", "패치 분류", "DINOv3", ["dinov3_vits16", "dinov3_vitb16", "resnet18", "convnext_tiny", "efficientnet_b0"], "dinov3_vits16", "패치 좌표·클래스·원본 해시 manifest", False, "weight_initialization", ["timm"]),
        ("ocr", "문자 인식", "CTC 문자 인식", ["ctc"], "ctc", "문자 이미지와 사람이 확인한 실제 정답 문자열", False, "weight_initialization", []),
        ("rotated_detection", "회전 객체 검출", "회전 박스 검출", ["rotated_detector"], "rotated_detector", "클래스·회전 박스 또는 polygon", False, "weight_initialization", []),
        ("defect_gan", "결함 이미지 생성", "GAN", ["defect_gan"], "defect_gan", "실제 결함 crop; 생성 결과는 검토 후 train에만 채택", False, "weight_initialization", []),
        ("enhancement", "이미지 개선", "입력·정답 쌍 학습", ["enhancement"], "enhancement", "개선 전 입력과 개선 정답 이미지 쌍", False, "weight_initialization", []),
    ]
    families = []
    for task, label, model, architectures, default, prerequisite, remote, continuation, dependencies in definitions:
        missing = [name for name in dependencies if find_spec(name) is None]
        families.append({
            "task": task, "label": label, "model": model,
            "architectures": architectures, "default_architecture": default,
            "prerequisite": prerequisite, "remote_training": remote,
            "continuation": continuation,
            "devices": ["cpu", "cuda", "mps"] if task in ("classification", "segmentation", "detection", "anomaly", "patch_classification") else ["cpu"],
            "stages": ["label", "train", "evaluate", "generate", "review", "export"] if task == "defect_gan" else ["label", "train", "evaluate", "flow", "export"],
            "dependencies": dependencies, "missing_dependencies": missing,
            "quality_approved": False,
        })
    return {"families": families, "schema_version": 1}
