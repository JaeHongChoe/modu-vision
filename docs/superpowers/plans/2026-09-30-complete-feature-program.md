# Industrial feature completion implementation plan

> **For agentic workers:** Use the implementation ledger and `docs/feature-program.json` as the authoritative scope. Execute independent modules in parallel and review each diff before integration.

**Goal:** 123개 세부 항목을 빠짐없이 구현하고 앱·서버·독립 실행까지 연결한다.

**Architecture:** 기존 React/Electron·FastAPI·PyTorch 구조를 유지한다. 모델·라벨·평가·자원·Runtime의 공통 계약을 작은 모듈로 나누고 원본 데이터와 부모 모델은 보존한다.

**Tech Stack:** Python 3.10+, PyTorch, React 18, TypeScript, FastAPI, ONNX Runtime, optional segmentation/VLM/OpenVINO/native SDK dependencies.

**Spec:** `docs/superpowers/specs/2026-09-30-complete-feature-program-design.md`

## Global constraints

- 분류·패치 분류·분할은 DINOv3 pretrained, 일반 검출은 YOLO pretrained를 기본으로 사용한다.
- 공개 코드·문서·UI에는 벤치마킹 업체명·발표자료·사용자 데이터·서버 비밀정보를 포함하지 않는다.
- 원본 데이터는 읽기 전용이며 QA와 모델 저장소는 별도 경로를 사용한다.
- 모든 기능을 계획에 포함한다. 하드웨어·정답 데이터가 없으면 구현 상태와 검증 제한을 구분하고 완료를 부풀리지 않는다.
- 원격 작업은 소유권·장치·예약·취소·연결 복구를 기록한다. 미지원 장치로 조용히 fallback하지 않는다.
- 이미 존재하는 34개 대응 코드 항목도 재검증 대상이며 일괄 완료로 표시하지 않는다.

## Review focus

- 배경만 존재하거나 두 번째 이후 결함 클래스만 있는 입력의 판정·mask 유지.
- 복수 객체·복수 클래스·빈 주석의 필터·지표·오류 탐색 정확성.
- 취소·앱 재시작·서버 연결 단절 후 작업과 저장 결과의 일관성.
- 다른 라벨 세트·변경된 데이터·부모 모델·배포 모델의 버전 바인딩.
- 의존성·가중치·장비가 없는 환경의 명확한 오류와 비지원 기능 표시.

## Execution ledger

- Baseline: a4d3610. Isolated checkout reused; baseline and subsystem regressions recorded in the implementation ledgers.
- The user explicitly requested planning and execution of all audited capabilities. Continue without repeat confirmation.
- Ruling: decompose into ten independently reviewable subsystems while retaining all 123 IDs; omission is detected by the scope validator.

The checked feature items below mean implemented and integrated. Hardware acceptance, manufacturing quality and individual native controls are tracked separately in the feature register and acceptance report.

## Ordered work

### P01: 판정·데이터 정확성

**Goal:** 클래스별 mask를 타일·ROI·Blob·패키지까지 유지하고 필수 개수·면적 범위·평균 밝기 규칙을 검증한다.

**Interfaces:** 기존 API 호환성을 유지한다. 공통 변경은 통합 담당에게 전달하며 새 계약은 모듈 테스트와 API 타입으로 고정한다.

- [x] **F021 클래스별 데이터 분포**
  - Files: `backend/api/routes_dataset.py`, `backend/api/routes_dataset.py`
  - Acceptance: 하나의 이미지에 서로 다른 두 클래스가 있을 때 두 클래스 필터 모두 같은 이미지를 포함하고 라벨 세트별 개수를 정확히 보여야 한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F027 Segmentation의 여러 결함 클래스**
  - Files: `backend/engine/grouped_dataset_views.py`, `backend/engine/trainer.py`, `backend/engine/flowchart_engine.py`, `backend/engine/flowchart_engine.py`
  - Acceptance: 결함 클래스2만 존재하는 입력도 앱·타일·ROI·Blob·패키지에서 같은 클래스와 마스크를 유지하며 NG여야 한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F073 Blob 크기·개수와 누락 판정**
  - Files: `backend/engine/flowchart_engine.py`, `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F074 분할 영역의 평균 밝기로 임계값 판정**
  - Files: `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F079 OCR 문자 규칙과 오인식 교정**
  - Files: `backend/engine/ocr.py`, `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F122 Probability Threshold로 예측 필터**
  - Files: `backend/engine/flowchart_engine.py`, `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F123 Size Threshold로 영역 필터**
  - Files: `backend/engine/flowchart_engine.py`, `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.

**Verify:** `/opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider backend/tests` and `npm run typecheck`; record focused regressions before broad verification.

### P02: AI 라벨링

**Goal:** 점·박스 mask, 이미지 positive/negative, 텍스트 결합, 크기·장치 필터, 배치·few-label 추천·검토를 연결한다.

**Interfaces:** 기존 API 호환성을 유지한다. 공통 변경은 통합 담당에게 전달하며 새 계약은 모듈 테스트와 API 타입으로 고정한다.

- [x] **F001 자연어 조건으로 대상 영역 찾기**
  - Files: `backend/engine/label_candidate_providers.py`, `backend/api/routes_label_candidates.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F002 이미지 예시로 대상 찾기**
  - Files: `backend/engine/label_candidate_providers.py`, `src/renderer/components/labeling/CandidateProviderControls.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F003 이미지 프롬프트의 제외 영역 지정**
  - Files: `backend/api/routes_label_candidates.py`, `src/renderer/components/labeling/CandidateProviderControls.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F004 이미지와 텍스트 조건을 결합한 프롬프트**
  - Files: `backend/api/routes_label_candidates.py`, `backend/engine/label_candidate_providers.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F005 긴 텍스트 프롬프트 입력**
  - Files: `backend/engine/label_candidate_providers.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F006 프롬프트에서 다각형 라벨 생성**
  - Files: `backend/engine/label_candidate_providers.py`, `backend/api/routes_label_candidates.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F007 클릭으로 객체 영역 선택**
  - Files: `backend/engine/labeling_ai.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F008 드래그한 박스 안에서 객체 선택**
  - Files: `src/renderer/components/labeling/LabelingCanvas.tsx`, `backend/api/routes_annotation.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F009 박스 라벨을 객체 다각형으로 변환**
  - Files: `backend/engine/labeling_ai.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F010 소량 수동 라벨로 자동 추천기를 학습·갱신**
  - Files: `backend/api/routes_label_suggestions.py`, `backend/api/routes_label_suggestions.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F011 완료 모델로 여러 이미지의 라벨 제안**
  - Files: `backend/api/routes_label_suggestions.py`, `backend/api/routes_label_suggestions.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F012 키워드로 여러 이미지 일괄 라벨링**
  - Files: `backend/api/routes_label_candidates.py`, `backend/api/routes_label_suggestions.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F013 라벨 후보를 객체 크기로 필터링**
  - Files: `backend/api/routes_label_suggestions.py`, `backend/api/routes_label_candidates.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F014 AI 라벨링 CPU·GPU 선택**
  - Files: `backend/api/routes_label_suggestions.py`, `backend/engine/label_candidate_providers.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.

**Verify:** `/opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider backend/tests` and `npm run typecheck`; record focused regressions before broad verification.

### P03: 데이터·권한·결과 버전

**Goal:** 태그 색상·플래그·상태 통계·라벨 버전·계정과 역할·공유 프로젝트·외부 mask·DICOM을 연결한다.

**Interfaces:** 기존 API 호환성을 유지한다. 공통 변경은 통합 담당에게 전달하며 새 계약은 모듈 테스트와 API 타입으로 고정한다.

- [x] **F015 여러 이름 있는 라벨 세트**
  - Files: `backend/engine/project_labelsets.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F016 이미지별 색상 태그**
  - Files: `backend/engine/dataset_metadata.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F017 라벨 세트에 사용자 플래그**
  - Files: `backend/engine/project_labelsets.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F018 모델에 Best·Important 플래그**
  - Files: `backend/engine/model_catalog.py`, `src/renderer/components/evaluation/ModelDeploymentPanel.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F019 라벨링 완료·미완료 수와 비율**
  - Files: `src/renderer/components/dataset/DatasetStudio.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F020 상태 통계를 눌러 이미지 필터링**
  - Files: `src/renderer/components/dataset/DatasetStudio.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F022 Train·Test·미사용·미분할 분포**
  - Files: `src/renderer/components/dataset/DatasetStudio.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F023 모델 결과를 라벨 세트별로 선택**
  - Files: `backend/api/routes_model_comparisons.py`, `backend/api/routes_evaluation_history.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F024 여러 작업자의 데이터 정리**
  - Files: `backend/engine/dataset_metadata.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F059 재평가 이력 보관**
  - Files: `backend/api/routes_evaluation.py`, `backend/engine/evaluation_history.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F060 재평가 결과를 부모 모델 아래 묶어 표시**
  - Files: `src/renderer/components/evaluation/EvaluationHistoryPanel.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F061 모델 목록의 임계값 설정 정보**
  - Files: `src/renderer/components/evaluation/ModelComparisonPanel.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F113 권한을 가진 client-server 공동 프로젝트**
  - Files: `backend/main.py`, `backend/engine/project_labelsets.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F114 원본·수정 데이터와 처리 이력 관리**
  - Files: `backend/engine/dataset_metadata.py`, `backend/engine/project_archive.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F115 JPG·PNG·BMP·TIFF 이미지 입력**
  - Files: `backend/engine/industrial_adapters.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F116 DCM·DICOM 이미지 입력**
  - Files: `backend/engine/industrial_adapters.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F117 외부 JSON 라벨 입력**
  - Files: `backend/engine/annotation_formats.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F118 외부 mask 라벨 이미지 입력**
  - Files: `backend/engine/grouped_dataset_views.py`, `backend/engine/annotation_formats.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.

**Verify:** `/opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider backend/tests` and `npm run typecheck`; record focused regressions before broad verification.

### P04: 모델과 자동 학습

**Goal:** 모든 모델군의 데이터 준비·학습·취소·평가·플로우·export를 연결하고 구조·HPO·증강·속도 목표 탐색을 추가한다.

**Interfaces:** 기존 API 호환성을 유지한다. 공통 변경은 통합 담당에게 전달하며 새 계약은 모듈 테스트와 API 타입으로 고정한다.

- [x] **F025 이미지 Classification**
  - Files: `backend/engine/trainer.py`, `backend/engine/model_backbones.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F026 고해상도 Patch Classification**
  - Files: `backend/engine/patch_classification.py`, `src/renderer/components/training/ModelFamilyCatalog.tsx`, `src/renderer/components/training/TrainingController.tsx`
  - Acceptance: 앱에서 패치 크기·간격·라벨을 준비해 DINOv3 학습을 시작·취소하고 완료 모델로 평가·플로우·패키지를 실행할 수 있어야 한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F028 일반 Object Detection**
  - Files: `backend/engine/trainer.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F029 Oriented Object Detection**
  - Files: `backend/engine/rotated_detection.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F030 OCR 문자 인식**
  - Files: `backend/engine/ocr.py`, `src/renderer/components/training/OCRWorkbench.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F031 학습형 정방향 Rotation 모델**
  - Files: `backend/engine/flow_operators.py`, `backend/engine/model_catalog.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F032 정상 데이터 기반 Anomaly Classification**
  - Files: `backend/engine/anomaly/dino_synthetic.py`, `backend/engine/anomaly/dino_synthetic.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F033 Anomaly Segmentation 영역 검사**
  - Files: `backend/engine/anomaly/dino_synthetic.py`, `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F034 GAN Defect Generator**
  - Files: `backend/engine/defect_gan.py`, `backend/engine/defect_gan.py`, `backend/api/routes_defect_gan.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F035 학습형 Image Enhancement**
  - Files: `backend/engine/enhancement.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F036 빠른 초기 모델 학습**
  - Files: `backend/engine/trainer.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F037 모델 구조 자동 탐색**
  - Files: `backend/engine/trainer.py`, `backend/engine/trainer.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F038 초매개변수 자동 탐색**
  - Files: `backend/engine/trainer.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F039 사전학습 모델을 활용한 Transfer Learning**
  - Files: `backend/engine/model_backbones.py`, `src/renderer/components/training/modelTrainingOptions.ts`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F040 기존 모델에서 추가 데이터 재학습**
  - Files: `backend/engine/warm_start.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F041 최적화된 설정을 재사용하는 Fast Retraining**
  - Files: `backend/engine/warm_start.py`, `backend/engine/trainer.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F042 추론 속도 목표를 반영한 학습 최적화**
  - Files: `backend/api/routes_evaluation.py`, `backend/engine/trainer.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F043 Embedded 장치에 맞춘 모델 최적화**
  - Files: `backend/engine/edge_runtime.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F044 양자화 모델 생성**
  - Files: `backend/engine/exporter.py`, `src/renderer/components/inference/InferenceCenterStudio.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F112 데이터 증강 방법 자동 선택**
  - Files: `backend/engine/augmentations.py`, `backend/engine/trainer.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.

**Verify:** `/opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider backend/tests` and `npm run typecheck`; record focused regressions before broad verification.

### P05: 평가와 비교

**Goal:** 객체·픽셀·문자 단위 오류, 분포·ROC, 이종 모델 비교, OBB 지표·산점도를 제공한다.

**Interfaces:** 기존 API 호환성을 유지한다. 공통 변경은 통합 담당에게 전달하며 새 계약은 모듈 테스트와 API 타입으로 고정한다.

- [x] **F047 이미지와 분석 결과를 같은 화면에서 확인**
  - Files: `src/renderer/components/evaluation/EvaluationStudio.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F048 정확도·정밀도·재현율·F1**
  - Files: `backend/api/routes_evaluation.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F049 클래스별 지표 표시**
  - Files: `backend/api/routes_evaluation.py`, `backend/api/routes_evaluation.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F050 Confusion Matrix 셀로 이미지 필터**
  - Files: `src/renderer/components/evaluation/EvaluationStudio.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F051 분할 클래스별 미검·과검 이미지 찾기**
  - Files: `backend/api/routes_evaluation.py`, `src/renderer/components/evaluation/EvaluationStudio.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F052 검출 클래스별 누락·추가 박스 이미지 찾기**
  - Files: `backend/api/routes_evaluation.py`, `src/renderer/components/evaluation/EvaluationStudio.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F053 OCR 문자별 누락·추가 결과 찾기**
  - Files: `src/renderer/components/training/OCRWorkbench.tsx`, `backend/engine/ocr.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F054 결과 확률 분포 분석**
  - Files: `src/renderer/components/evaluation/EvaluationStudio.tsx`, `src/renderer/components/evaluation/EvaluationStudio.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F055 결함 크기 분포 분석**
  - Files: `src/renderer/components/evaluation/EvaluationStudio.tsx`, `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F056 ROC 곡선·임계값 검토**
  - Files: `backend/api/routes_evaluation.py`, `src/renderer/components/evaluation/EvaluationStudio.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F057 같은 종류 모델 두 개 비교**
  - Files: `backend/api/routes_model_comparisons.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F058 서로 다른 종류 모델 비교**
  - Files: `backend/api/routes_model_comparisons.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F062 회전 검출 mAP·IoU 평가**
  - Files: `backend/engine/rotated_detection.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F063 회전 박스 IoU·각도 오류 분포**
  - Files: `src/renderer/components/training/RotatedDetectionPanel.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.

**Verify:** `/opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider backend/tests` and `npm run typecheck`; record focused regressions before broad verification.

### P06: 플로우·측정·생성 UI

**Goal:** 실행 가능한 DAG·ROI·정렬·패치·병렬 노드·측정·OBB 그리기·GAN 합성을 완성한다.

**Interfaces:** 기존 API 호환성을 유지한다. 공통 변경은 통합 담당에게 전달하며 새 계약은 모듈 테스트와 API 타입으로 고정한다.

- [x] **F064 여러 모델을 잇는 플로우 편집**
  - Files: `src/renderer/components/flowchart/FlowchartStudio.tsx`, `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F065 모델 뒤에 모델을 잇는 다단계 검사**
  - Files: `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F066 클래스에 따라 후속 검사 분기**
  - Files: `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F067 한 모델을 여러 클래스에서 공유**
  - Files: `backend/engine/flowchart_engine.py`, `backend/engine/flow_package.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F068 여러 모델 결과를 합쳐 최종 판정**
  - Files: `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F069 고정 ROI 검사**
  - Files: `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F070 검출 객체의 ROI로 후속 검사**
  - Files: `backend/engine/flowchart_engine.py`, `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F071 회전 객체의 Fitted ROI와 정방향 후속 검사**
  - Files: `backend/engine/flow_operators.py`, `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F072 ROI를 원해상도 패치로 분할**
  - Files: `backend/engine/flow_operators.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F075 곡선·자유 경로 길이 측정**
  - Files: `src/renderer/components/evaluation/PhysicalScaleOverlay.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F076 검출 영역 넓이 측정**
  - Files: `backend/engine/flowchart_engine.py`, `src/renderer/components/evaluation/PhysicalScaleOverlay.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F077 GAN 원본 이미지의 생성 위치 지정**
  - Files: `backend/engine/defect_gan.py`, `backend/api/routes_defect_gan.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F078 GAN 여러 생성 영역 지정**
  - Files: `backend/engine/defect_gan.py`, `backend/engine/defect_gan.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F080 OBB 중심 대칭 방식 라벨링**
  - Files: `src/renderer/components/labeling/LabelingCanvas.tsx`, `src/renderer/components/labeling/LabelingCanvas.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F081 OBB 한쪽 면을 기준으로 라벨링**
  - Files: `src/renderer/components/labeling/LabelingCanvas.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F082 OBB 비정형 객체용 라벨링**
  - Files: `src/renderer/components/labeling/LabelingCanvas.tsx`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F090 전체 플로우를 하나의 호출로 실행**
  - Files: `backend/engine/flow_package.py`, `backend/engine/flow_package_runtime.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F091 여러 모델의 순차·병렬 추론**
  - Files: `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F119 저장 플로우로 사전 단일·일괄 검사**
  - Files: `src/renderer/components/inference/InferenceCenterStudio.tsx`, `backend/api/routes_inspections.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.

**Verify:** `/opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider backend/tests` and `npm run typecheck`; record focused regressions before broad verification.

### P07: 서버와 GPU 배분

**Goal:** 모든 작업군의 원격 실행·예약·동시 배치·MIG·다중 GPU 학습·취소 복구를 연결한다.

**Interfaces:** 기존 API 호환성을 유지한다. 공통 변경은 통합 담당에게 전달하며 새 계약은 모듈 테스트와 API 타입으로 고정한다.

- [x] **F083 여러 GPU에서 서로 다른 학습 작업 동시 실행**
  - Files: `backend/engine/shared_scheduler.py`, `backend/engine/shared_scheduler.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F084 같은 GPU에 가벼운 모델 여러 개 동시 배치**
  - Files: `backend/engine/shared_scheduler.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F085 학습·AI 라벨링·추론 작업을 GPU별 배분**
  - Files: `backend/engine/shared_scheduler.py`, `backend/api/routes_label_suggestions.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F086 하나의 큰 모델을 여러 GPU로 학습**
  - Files: `backend/engine/trainer.py`, `backend/remote/worker.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F087 MIG 논리 GPU 할당**
  - Files: `backend/engine/shared_scheduler.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F088 학습 작업 등록·상태·대기열 API**
  - Files: `backend/api/routes_training.py`, `backend/engine/shared_scheduler.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F089 AI 라벨링·전문 모델의 서버 자원 사용**
  - Files: `backend/remote/worker.py`, `backend/engine/model_catalog.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.

**Verify:** `/opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider backend/tests` and `npm run typecheck`; record focused regressions before broad verification.

### P08: 독립 학습 Engine

**Goal:** 통합 CLI·REST의 폴더 입력·출력·옵션·진행률·취소·재현 설정을 제공한다.

**Interfaces:** 기존 API 호환성을 유지한다. 공통 변경은 통합 담당에게 전달하며 새 계약은 모듈 테스트와 API 타입으로 고정한다.

- [x] **F045 GUI 없이 폴더 입력으로 학습 REST API**
  - Files: `backend/api/routes_training.py`, `backend/api/routes_project.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F046 하나의 CLI로 quick·AutoDL 학습**
  - Files: `backend/engine/trainer.py`, `backend/remote/worker.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F102 학습 모델 파일 출력**
  - Files: `backend/engine/exporter.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F103 학습 설정 JSON 출력·재사용**
  - Files: `backend/engine/training_provenance.py`, `backend/engine/trainer.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F104 평가 지표 JSON 출력**
  - Files: `backend/engine/evaluation_history.py`, `backend/api/routes_model_comparisons.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F105 이미지별 예측 JSON 출력**
  - Files: `backend/api/routes_inspections.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F106 외부 UI에 학습 기능을 통합**
  - Files: `backend/api/routes_training.py`, `backend/api/routes_ocr.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F107 외부 JSON 라벨로 현장 재학습**
  - Files: `backend/engine/annotation_formats.py`, `backend/engine/grouped_dataset_views.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F120 학습 CLI의 지정 출력 폴더**
  - Files: `backend/engine/trainer.py`, `backend/api/routes_export.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F121 학습 CLI help·진행률·지표 출력**
  - Files: `backend/api/routes_training.py`, `backend/remote/worker.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.

**Verify:** `/opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider backend/tests` and `npm run typecheck`; record focused regressions before broad verification.

### P09: 배포 Runtime와 장치

**Goal:** 검사 deadline·Python/C++/C# SDK·OpenVINO·양자화·CPU/GPU/Edge 패키지를 완성한다.

**Interfaces:** 기존 API 호환성을 유지한다. 공통 변경은 통합 담당에게 전달하며 새 계약은 모듈 테스트와 API 타입으로 고정한다.

- [x] **F092 추론 최대 시간 제한**
  - Files: `backend/engine/inspection_service.py`, `backend/engine/flowchart_engine.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F093 C++ 네이티브 Runtime 연동**
  - Files: `backend/engine/flow_package_runtime.py`, `backend/engine/inspection_service.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F094 C# 네이티브 Runtime 연동**
  - Files: `examples/InspectionServiceClient.cs`, `backend/engine/flow_package_runtime.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F095 Python Runtime 연동**
  - Files: `backend/engine/flow_package_runtime.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F096 Predictor·Executor 호출 구조**
  - Files: `backend/engine/flow_package_runtime.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F097 CPU 추론**
  - Files: `backend/engine/edge_runtime.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F098 CUDA GPU 추론**
  - Files: `backend/remote/coordinator.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F099 Intel OpenVINO CPU·GPU·iGPU·NPU**
  - Files: `backend/engine/edge_runtime.py`, `backend/engine/exporter.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F100 Jetson 등 Embedded 보드 실행**
  - Files: `backend/engine/edge_runtime.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F101 추가 변환 작업 없이 하드웨어에 적용**
  - Files: `backend/engine/exporter.py`, `backend/engine/edge_runtime.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.

**Verify:** `/opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider backend/tests` and `npm run typecheck`; record focused regressions before broad verification.

### P10: 운영 자동화

**Goal:** 수집→후보 라벨→검수/정책→재학습→동일 holdout 비교→승인→적용→rollback을 자동 작업으로 연결한다.

**Interfaces:** 기존 API 호환성을 유지한다. 공통 변경은 통합 담당에게 전달하며 새 계약은 모듈 테스트와 API 타입으로 고정한다.

- [x] **F108 높은 신뢰도 예측을 자동 라벨로 사용**
  - Files: `backend/api/routes_label_suggestions.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F109 새 데이터에서 자동 재학습·재배포**
  - Files: `backend/api/routes_model_deployments.py`, `backend/engine/managed_service.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F110 현장과 중앙 서버를 묶어 모델 관리**
  - Files: `backend/remote/coordinator.py`, `backend/api/routes_model_deployments.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.
- [x] **F111 장비에서 학습과 추론 동시 운용**
  - Files: `backend/engine/managed_service.py`, `backend/engine/shared_scheduler.py`
  - Acceptance: 실제 입력에서 동작·오류·취소·저장·재열기·후속 단계·독립 실행 결과를 확인한다.
  - Red: 누락 기능/잘못된 판정에 대한 실패 재현 → 구현 → 회귀 통과 → API/UI/실제 입력 증거 기록.

**Verify:** `/opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider backend/tests` and `npm run typecheck`; record focused regressions before broad verification.

## Final acceptance

- [ ] Scope validator confirms F001–F123 are unique and all assigned; no pending feature is silently removed.
- [ ] Full backend suite, renderer/main typecheck and build.
- [ ] Native app path: import → label → split → each family training/cancel → evaluate/compare → flow → inspect/history/export → reopen.
- [ ] Independent CLI/flow package and SDK contract runs; available remote/CPU devices verified with ownership receipts.
- [ ] Separate missing hardware/quality evidence from implemented functional behavior.
- [ ] No benchmark company strings or private material in tracked public changes; review before normal main integration.
