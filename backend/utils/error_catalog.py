"""
backend/utils/error_catalog.py

Complete 8-Item Bilingual Korean/English Industrial Error Catalog
for Vision AI Studio (Shop-Floor Diagnostic & Auto-Remediation System).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ErrorSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ErrorCatalogItem(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    code: str = Field(..., description="Unique error identifier e.g. ERR_001")
    title_en: str
    title_kr: str
    description_en: str
    description_kr: str
    remediation_en: str
    remediation_kr: str
    severity: ErrorSeverity
    auto_fixable: bool
    cause_en: Optional[str] = None
    cause_kr: Optional[str] = None
    action: Optional[str] = None
    details: Optional[Any] = None

    # Convenient alias properties for cross-milestone compatibility
    @property
    def error_code(self) -> str:
        return self.code

    @property
    def title_ko(self) -> str:
        return self.title_kr

    @property
    def description_ko(self) -> str:
        return self.description_kr

    @property
    def message_en(self) -> str:
        return self.description_en

    @property
    def message_ko(self) -> str:
        return self.description_kr

    @property
    def message_kr(self) -> str:
        return self.description_kr

    @property
    def cause_ko(self) -> Optional[str]:
        return self.cause_kr

    @property
    def remediation_ko(self) -> str:
        return self.remediation_kr

    @property
    def remediation(self) -> str:
        return self.remediation_kr

    def to_ws_payload(self) -> Dict[str, Any]:
        """Format for WebSocket telemetry transmission per PROJECT.md line 161."""
        return {
            "error_code": self.code,
            "message_ko": self.message_ko,
            "message_en": self.message_en,
            "remediation": self.remediation_ko,
            "remediation_en": self.remediation_en,
            "remediation_kr": self.remediation_kr,
            "title_en": self.title_en,
            "title_kr": self.title_kr,
            "title_ko": self.title_kr,
            "action": self.action or "check_logs",
            "severity": self.severity.value,
            "auto_fixable": self.auto_fixable,
            "details": self.details,
        }


# ============================================================================
# Master 8-Item Bilingual Error Definitions
# ============================================================================

CATALOG: Dict[str, ErrorCatalogItem] = {
    "ERR_001": ErrorCatalogItem(
        code="ERR_001",
        title_en="Out of Memory (OOM)",
        title_kr="메모리 부족 (OOM)",
        description_en=(
            "The system or hardware accelerator ran out of memory during tensor allocation "
            "or model execution."
        ),
        description_kr=(
            "배치 연산 또는 모델 실행 중 GPU/가속기 또는 시스템 메모리가 부족하여 작업이 중단되었습니다."
        ),
        remediation_en=(
            "Reduce training batch size (e.g. from 16 to 8 or 4), reduce input image resolution, "
            "or switch to the '⚡ Fast Prototype' preset."
        ),
        remediation_kr=(
            "학습 배치 크기를 줄이거나(예: 16 -> 8 또는 4), 입력 이미지 해상도를 낮추거나, "
            "'⚡ 빠른 프로토타입(Fast Prototype)' 프리셋으로 전환하십시오."
        ),
        cause_en="Batch size or image resolution exceeds available GPU VRAM or MPS unified memory.",
        cause_kr="배치 크기나 이미지 해상도가 현재 GPU VRAM 또는 MPS 통합 메모리 용량을 초과했습니다.",
        action="reduce_batch_size",
        severity=ErrorSeverity.CRITICAL,
        auto_fixable=True,
    ),
    "ERR_002": ErrorCatalogItem(
        code="ERR_002",
        title_en="Insufficient Training Samples",
        title_kr="학습 데이터 부족",
        description_en=(
            "The dataset does not contain the minimum required number of samples per class "
            "(at least 2 samples per class required for train/val splitting; recommended minimum 10)."
        ),
        description_kr=(
            "데이터셋의 클래스별 샘플 수가 최소 기준에 미달합니다 "
            "(학습/검증 세트 분할을 위해 클래스당 최소 2개 이상 필요, 권장 최소 10개 이상)."
        ),
        remediation_en=(
            "Add more labeled images to the deficient classes or execute the Procedural Synthetic "
            "Dataset Generator to augment defective samples."
        ),
        remediation_kr=(
            "부족한 클래스에 라벨링된 이미지를 추가하거나 절차적 합성 데이터 생성기(Synthetic Generator)를 "
            "실행하여 결함 샘플을 보충하십시오."
        ),
        cause_en="Supervised vision models require sufficient labeled examples across all classes for statistical learning.",
        cause_kr="지도 학습 비전 모델은 통계적 일반화를 위해 모든 클래스에 걸쳐 충분한 라벨링된 샘플이 필요합니다.",
        action="open_synthetic_dialog",
        severity=ErrorSeverity.HIGH,
        auto_fixable=False,
    ),
    "ERR_003": ErrorCatalogItem(
        code="ERR_003",
        title_en="Extreme Class Imbalance",
        title_kr="극심한 클래스 불균형",
        description_en=(
            "The sample ratio between the majority class and minority defect class exceeds "
            "the recommended tolerance threshold (ratio > 20:1), risking model bias toward the dominant class."
        ),
        description_kr=(
            "다수 클래스(정상/OK)와 소수 결함(NG) 클래스 간의 샘플 비율 차이가 허용 한도(20:1 초과)를 초과하여 "
            "결함 검출 실패 및 편향이 발생할 위험이 있습니다."
        ),
        remediation_en=(
            "Enable class-weighted loss rebalancing, activate industrial data augmentations "
            "(cutout, rotation, lighting), or acquire additional minority class samples."
        ),
        remediation_kr=(
            "가중치 적용 손실 함수(Weighted Cross-Entropy) 활성화, 결함 부위 데이터 증강(Augmentation) 활성화, "
            "또는 결함 클래스 샘플을 보충하십시오."
        ),
        cause_en="Severe disparity in sample frequencies causes the optimization loss to neglect minority defect categories.",
        cause_kr="샘플 빈도의 극심한 격차로 인해 최적화 손실이 소수 결함 범주를 무시하게 됩니다.",
        action="rebalance_classes",
        severity=ErrorSeverity.MEDIUM,
        auto_fixable=True,
    ),
    "ERR_004": ErrorCatalogItem(
        code="ERR_004",
        title_en="Invalid Bounding Box Coordinates",
        title_kr="잘못된 바운딩 박스",
        description_en=(
            "Detected bounding box with zero area, negative dimensions, or inverted coordinates "
            "(xmin >= xmax or ymin >= ymax) exceeding image boundaries."
        ),
        description_kr=(
            "면적이 0이거나 음수인 크기, 좌표 역전(xmin >= xmax 또는 ymin >= ymax), 또는 "
            "이미지 경계를 벗어난 비정상 바운딩 박스가 발견되었습니다."
        ),
        remediation_en=(
            "Sanitize coordinates by automatically swapping inverted bounds, clamping to image boundaries, "
            "and discarding degenerate zero-area boxes."
        ),
        remediation_kr=(
            "좌표 자동 역전 교정(Swap) 및 이미지 경계 클램핑을 적용하고, "
            "면적이 0인 불량 박스를 자동 필터링하거나 라벨링 캔버스에서 다시 그리십시오."
        ),
        cause_en="Pointer drag glitch or coordinate conversion inversion generated degenerate bounding boxes.",
        cause_kr="라벨링 드래그 오류 또는 좌표 변환 역전으로 유효하지 않은 바운딩 박스가 생성되었습니다.",
        action="auto_sanitize_bboxes",
        severity=ErrorSeverity.HIGH,
        auto_fixable=True,
    ),
    "ERR_005": ErrorCatalogItem(
        code="ERR_005",
        title_en="Corrupted Image File",
        title_kr="손상된 이미지 파일",
        description_en=(
            "Failed to decode image file due to 0-byte file size, corrupt magic byte headers, "
            "truncated bitstream, or unsupported format."
        ),
        description_kr=(
            "0바이트 파일, 손상된 매직 바이트 헤더, 비트스트림 잘림 또는 지원되지 않는 형식으로 인해 "
            "이미지 디코딩에 실패했습니다."
        ),
        remediation_en=(
            "Inspect file integrity, delete or replace the corrupted file, or re-export the image "
            "in standard PNG/JPEG/BMP format."
        ),
        remediation_kr=(
            "파일 무결성을 확인하고 손상된 이미지를 프로젝트에서 제거하거나 표준 PNG/JPEG/BMP 형식으로 "
            "다시 저장하십시오."
        ),
        cause_en="File transfer failure, disk corruption, or empty file generation resulted in unreadable image headers.",
        cause_kr="파일 전송 중단, 디스크 손상 또는 0바이트 생성으로 인해 이미지 헤더 디코딩이 불가능합니다.",
        action="remove_corrupt_images",
        severity=ErrorSeverity.HIGH,
        auto_fixable=True,
    ),
    "ERR_006": ErrorCatalogItem(
        code="ERR_006",
        title_en="Anomaly NG Defect in Normal Set",
        title_kr="비지도 이상 탐지 정상 세트 오염",
        description_en=(
            "The unsupervised anomaly detection training split contains defect (NG) samples or masks. "
            "Unsupervised anomaly models (PaDiM/PatchCore) must be trained strictly on 100% normal (OK) images."
        ),
        description_kr=(
            "비지도 이상 탐지(PaDiM/PatchCore) 학습 세트에 결함(NG) 이미지가 포함되어 있습니다. "
            "비지도 모델은 100% 정상(OK) 데이터로만 학습되어야 합니다."
        ),
        remediation_en=(
            "Move all defect (NG) images and masks from the train split into the test/validation split, "
            "keeping only pure normal (OK/good) images in training."
        ),
        remediation_kr=(
            "학습 세트(train/good)에 있는 결함 이미지를 검증/테스트 세트(test/defect)로 이동하고 "
            "학습 폴더에는 순수 정상(OK) 이미지만 유지하십시오."
        ),
        cause_en="Unsupervised feature distribution estimation requires zero defect contamination in normal training data.",
        cause_kr="비지도 정상 분포 추정 모델은 학습 데이터에 결함이 완전히 배제되어야 정상 기준을 확립할 수 있습니다.",
        action="move_defects_to_test",
        severity=ErrorSeverity.CRITICAL,
        auto_fixable=False,
    ),
    "ERR_007": ErrorCatalogItem(
        code="ERR_007",
        title_en="Device Acceleration Fallback Warning",
        title_kr="가속기 폴백 경고",
        description_en=(
            "Hardware acceleration (CUDA or Metal MPS) was unavailable or initialization failed, "
            "triggering transparent CPU fallback with reduced throughput."
        ),
        description_kr=(
            "요청된 하드웨어 가속기(CUDA 또는 Metal MPS)를 초기화할 수 없어 CPU로 자동 폴백되었습니다. "
            "학습 속도가 저하될 수 있습니다."
        ),
        remediation_en=(
            "Verify GPU drivers (GPU driver & CUDA toolkit) or Metal GPU compatibility on macOS. "
            "Training will proceed normally on CPU."
        ),
        remediation_kr=(
            "GPU 드라이버(GPU 드라이버/CUDA 툴킷) 설치 상태를 확인하거나 CPU 모드로 계속 진행하십시오. "
            "(CPU 모드에서도 모든 기능은 정상 동작합니다.)"
        ),
        cause_en="Requested compute device was not detected by PyTorch runtime, defaulting to host CPU.",
        cause_kr="요청한 가속 디바이스를 PyTorch 런타임에서 초기화할 수 없어 호스트 CPU로 안전하게 전환되었습니다.",
        action="continue_on_cpu",
        severity=ErrorSeverity.LOW,
        auto_fixable=True,
    ),
    "ERR_008": ErrorCatalogItem(
        code="ERR_008",
        title_en="Training Divergence (NaN Loss)",
        title_kr="학습 발산 (NaN 손실)",
        description_en=(
            "Training loss evaluated to NaN or Inf values due to an excessively high learning rate, "
            "numerical instability in gradient calculations, or unnormalized inputs."
        ),
        description_kr=(
            "학습 손실(Loss)이 NaN 또는 무한대(Inf)로 발산했습니다. 과도하게 높은 학습률(Learning Rate)이나 "
            "수치적 불안정성으로 인해 발생합니다."
        ),
        remediation_en=(
            "Lower the learning rate (e.g. reduce by 5x or 10x), enable gradient clipping, "
            "increase LR warmup epochs, or use the Fast Prototype preset."
        ),
        remediation_kr=(
            "학습률을 5배~10배 낮추고, 그래디언트 클리핑(Grad Clipping)을 적용하거나 "
            "웜업 에포크(Warmup Epochs)를 늘려 다시 시작하십시오."
        ),
        cause_en="Extreme gradient updates exploded network weights, producing non-finite loss values.",
        cause_kr="급격한 역전파 그래디언트로 인해 신경망 가중치가 오버플로우되어 손실값이 NaN으로 산출되었습니다.",
        action="reduce_learning_rate",
        severity=ErrorSeverity.CRITICAL,
        auto_fixable=True,
    ),
}

# Mapping legacy or semantic codes to canonical codes
_ALIAS_MAP = {
    "ERR_GPU_OOM": "ERR_001",
    "ERR_OOM": "ERR_001",
    "ERR_INSUFFICIENT_SAMPLES": "ERR_002",
    "ERR_NO_DATA": "ERR_002",
    "ERR_SINGLE_CLASS": "ERR_002",
    "ERR_CLASS_IMBALANCE": "ERR_003",
    "ERR_DEGENERATE_BBOX": "ERR_004",
    "ERR_CORRUPT_IMAGE": "ERR_005",
    "ERR_ANOMALY_DEFECT_IN_TRAIN": "ERR_006",
    "ERR_ANOMALY_CONTAMINATION": "ERR_006",
    "ERR_DEVICE_FALLBACK": "ERR_007",
    "ERR_TRAINING_DIVERGENCE": "ERR_008",
    "ERR_NAN_LOSS": "ERR_008",
}


def get_error(code: str) -> Optional[ErrorCatalogItem]:
    """Retrieve error item by code (e.g. 'ERR_001', 'err_001', 'ERR_GPU_OOM')."""
    if not code:
        return None
    normalized = code.upper().strip()
    if normalized in CATALOG:
        return CATALOG[normalized]
    if normalized in _ALIAS_MAP:
        return CATALOG[_ALIAS_MAP[normalized]]
    return None


def get_all_errors() -> Dict[str, ErrorCatalogItem]:
    """Return all 8 registered catalog items."""
    return CATALOG.copy()


def classify_exception(exc: Exception, details: Optional[str] = None) -> ErrorCatalogItem:
    """Classifies any Python/PyTorch exception into the bilingual error catalog."""
    msg = str(exc).lower()

    if "out of memory" in msg or "cuda error: out of memory" in msg or "mps backend out of memory" in msg:
        canonical_code = "ERR_001"
    elif "must contain exclusively normal" in msg or "anomaly training split" in msg or "ng in normal" in msg:
        canonical_code = "ERR_006"
    elif "corrupt" in msg or "0 byte" in msg or "header magic byte" in msg or "cannot identify image" in msg:
        canonical_code = "ERR_005"
    elif "degenerate" in msg or "inverted" in msg or "box width" in msg or "xmin >=" in msg:
        canonical_code = "ERR_004"
    elif "nan" in msg or "inf" in msg or "divergence" in msg or "loss exploded" in msg:
        canonical_code = "ERR_008"
    elif "imbalance" in msg or "ratio > 20" in msg:
        canonical_code = "ERR_003"
    elif "no images" in msg or "single class" in msg or "at least 2 classes" in msg or "insufficient" in msg:
        canonical_code = "ERR_002"
    elif "fallback" in msg or "not available on this host" in msg:
        canonical_code = "ERR_007"
    else:
        # Fallback to general representation
        return ErrorCatalogItem(
            code="ERR_UNKNOWN",
            title_en="System Error",
            title_kr="시스템 오류",
            description_en=f"An unexpected error occurred during processing: {str(exc)}",
            description_kr=f"처리 중 예상치 못한 오류가 발생했습니다: {str(exc)}",
            remediation_en="Check system logs and retry, or contact support.",
            remediation_kr="로그를 확인하고 다시 시도하거나 고객지원에 문의하십시오.",
            cause_en="An internal runtime exception was raised.",
            cause_kr="내부 런타임 예외가 발생했습니다.",
            action="check_logs",
            severity=ErrorSeverity.HIGH,
            auto_fixable=False,
            details=details or str(exc),
        )

    base = CATALOG[canonical_code]
    if details:
        return ErrorCatalogItem(
            code=base.code,
            title_en=base.title_en,
            title_kr=base.title_kr,
            description_en=base.description_en,
            description_kr=base.description_kr,
            remediation_en=base.remediation_en,
            remediation_kr=base.remediation_kr,
            cause_en=base.cause_en,
            cause_kr=base.cause_kr,
            action=base.action,
            severity=base.severity,
            auto_fixable=base.auto_fixable,
            details=details,
        )
    return base


def format_error_response(
    code: str,
    details: Optional[Any] = None,
    custom_message_en: Optional[str] = None,
    custom_message_kr: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Format a standardized JSON error response body matching the frontend contract.
    """
    err = get_error(code)
    if err is None:
        return {
            "status": "error",
            "error_code": code,
            "title_en": "Unknown Error",
            "title_kr": "알 수 없는 오류",
            "title_ko": "알 수 없는 오류",
            "description_en": custom_message_en or f"Unregistered error code {code}",
            "description_kr": custom_message_kr or f"등록되지 않은 오류 코드 {code}",
            "message_en": custom_message_en or f"Unregistered error code {code}",
            "message_ko": custom_message_kr or f"등록되지 않은 오류 코드 {code}",
            "remediation_en": "Contact system support.",
            "remediation_kr": "시스템 관리자에게 문의하십시오.",
            "remediation": "시스템 관리자에게 문의하십시오.",
            "severity": ErrorSeverity.HIGH.value,
            "auto_fixable": False,
            "details": details,
        }

    return {
        "status": "error",
        "error_code": code.upper().strip(),
        "canonical_code": err.code,
        "title_en": err.title_en,
        "title_kr": err.title_kr,
        "title_ko": err.title_kr,
        "description_en": custom_message_en or err.description_en,
        "description_kr": custom_message_kr or err.description_kr,
        "message_en": custom_message_en or err.description_en,
        "message_ko": custom_message_kr or err.description_kr,
        "cause_en": err.cause_en,
        "cause_kr": err.cause_kr,
        "remediation_en": err.remediation_en,
        "remediation_kr": err.remediation_kr,
        "remediation": err.remediation_kr,
        "action": err.action,
        "severity": err.severity.value,
        "auto_fixable": err.auto_fixable,
        "details": details,
    }
