# 전체 모델 실행과 평가 실행 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. 이 문서는 검토용이며 새로운 구현 완료 상태가 아니다.

**Goal:** 10개 모델군의 준비·학습·평가·활용·패키징을 동일한 작업 방식으로 연결한다.

**Architecture:** Windows local-first 모듈형 API/worker/runtime 계약을 확장한다. 기존 프로젝트와 기능을 유지하며 영속 작업과 revision 검증을 공통으로 사용한다.

**Tech Stack:** React/TypeScript/Electron, Python/FastAPI/PyTorch, SQLite 및 versioned storage adapter.

**Spec:** ../specs/2026-10-02-windows-open-source-service-design.md

## 공통 조건

- Windows11 x64 우선, Linux GPU worker, 개인 모드와 선택 팀 서버. 지원표에 없는 조합은 preflight에서 차단한다.
- DINOv3 CLS/SEG/Patch와 YOLO detection 기본을 유지한다. 새로운 pretrained/OBB adapter와 distribution license는 출처·조건 검토 후 적용한다.
- 원본과 타 작업·프로세스를 보존한다. private source data와 비밀정보를 소스·예제·검증 로그·공개 artifact에 포함하지 않는다.
- 각 파일/함수 이름은 계획상 신규 계약이다. existing API compatibility를 확인하고 필요한 adapter와 migration을 같은 task에서 검증한다.
- 새로운 implementation/native/quality/signing/physical acceptance를 이전 test counts로 대체하지 않는다.

## 검토에서 빠지기 쉬운 입력

- 한글/공백/긴 Windows 경로, read-only 또는 locked file: 원본 보존과 구체적인 복구 행동.
- 작업/transfer/update/approval 중 접속 종료와 다시 시작: 동일 ID·revision·owner의 상태를 복구.
- 빈 정상 annotation과 UNKNOWN truth: 정상 판정이나 품질 pass로 자동 변환하지 않음.
- 두 사용자 또는 두 attempt의 동시 변경: revision/fencing/권한 확인으로 덮어쓰기와 중복 결과 방지.
- UI 선택 버전과 실행된 artifact가 다름: 실제 실행 identity·stale 사유·다음 행동 표시.

## task 실행 방법

각 task는 아래 acceptance를 먼저 실패 테스트로 고정한 후 구현한다. 신규 test 파일은 제안 위치이며 기존 테스트와 중복되는 경우 해당 production 경로의 회귀를 확장한다. UI smoke는 실제 Windows/Electron 또는 host adapter가 같은 renderer의 browser E2E로 확인한다. 실제 Windows native 증거는 browser 테스트와 구분한다.

## S4-01 DINOv3 Classification recipe

**Files:**

- Modify: `backend/engine/model_backbones.py`
- Modify: `backend/engine/trainer.py`
- Modify: `src/renderer/components/training/ModelFamilyCatalog.tsx`
- Test: `backend/tests/test_service_s4_01.py`

**Interfaces:** `ClassificationRecipe(backbone, train_mode, class_roles, score_spec)`

**Consumes:** S1-05, S3-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 실제 DINOv3 출처/hash를 표시하고 head-only 기본·선택형 partial/full fine-tune의 범위·VRAM·resume 차이를 설명한다.
- 다중 class label→train→confusion/threshold→flow→export를 로컬 CPU/CUDA 및 선택 remote 지원표에서 검증한다. 불가능한 자원 조합은 제출 전 차단한다.
- 기존 지원 backbone/decoder checkpoint의 offline 재구성·평가·flow/export compatibility를 fixture로 유지한다. 신규 adapter가 기존 config/좌표/score 의미를 바꾸면 명시적 version migration을 요구한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s4_01.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s4_01.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S4-02 DINOv3 Segmentation recipe

**Files:**

- Modify: `backend/engine/model_backbones.py`
- Modify: `backend/engine/grouped_dataset_views.py`
- Extend directory: `backend/engine/segmentation`
- Extend directory: `src/renderer/components/training`
- Test: `backend/tests/test_service_s4_02.py`

**Interfaces:** `SegmentationRecipe(backbone, decoder, tile_policy, class_roles)`

**Consumes:** S0-01, S1-05, S3-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- DINOv3를 유지하면서 dense decoder·tile/overlap·고해상도 mask·원본좌표 복원·Blob 면적/개수 규칙을 recipe로 정의한다.
- 실제 mask loader 증강과 pixel metric→flow→export를 검증한다. 작은 결함 품질 개선은 고정 검토 cohort 측정으로만 주장한다.
- 기존 지원 backbone/decoder checkpoint의 offline 재구성·평가·flow/export compatibility를 fixture로 유지한다. 신규 adapter가 기존 config/좌표/score 의미를 바꾸면 명시적 version migration을 요구한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s4_02.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s4_02.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S4-03 DINOv3 Patch Classification recipe

**Files:**

- Modify: `backend/engine/patch_classification.py`
- Modify: `src/renderer/components/training/PatchClassificationWorkbench.tsx`
- Test: `backend/tests/test_service_s4_03.py`

**Interfaces:** `PatchRecipe(backbone=DINOv3, patch_size, stride, aggregation, coordinate_map)`

**Consumes:** S1-05, S3-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- DINOv3 patch label·sample viewer·stride/overlap·경계 patch·원본 patch provenance를 연결한다.
- patch voting/max/NG count·threshold·전체 이미지 결과·heatmap·flow/export가 같은 규칙을 사용하며 원본 그룹 단위 split 누출이 없다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s4_03.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s4_03.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S4-04 YOLO 일반 검출 recipe

**Files:**

- Modify: `backend/engine/model_backbones.py`
- Modify: `backend/engine/dataset_loaders.py`
- Extend directory: `backend/engine/detection`
- Extend directory: `src/renderer/components/training`
- Test: `backend/tests/test_service_s4_04.py`

**Interfaces:** `DetectionRecipe(backbone=YOLO, nms, class_roles, score_spec)`

**Consumes:** S0-01, S1-05, S3-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- YOLO 기본·빈 정상 이미지·여러 object·작은 box·class index·resize/crop 좌표를 검증하고 checkpoint를 선택한 validation metric으로 관리한다.
- box label→train→mAP/오류 viewer→검출 ROI→다음 모델→전체flow export를 실제 같은 이미지로 검증한다.
- 기존 지원 backbone/decoder checkpoint의 offline 재구성·평가·flow/export compatibility를 fixture로 유지한다. 신규 adapter가 기존 config/좌표/score 의미를 바꾸면 명시적 version migration을 요구한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s4_04.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s4_04.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S4-05 이상탐지 두 목적과 calibration

**Files:**

- Extend directory: `backend/engine/anomaly`
- Modify: `src/renderer/components/training/DinoSyntheticOptions.tsx`
- Modify: `src/renderer/services/modelExecution.ts`
- Test: `backend/tests/test_service_s4_05.py`

**Interfaces:** `AnomalyRecipe(purpose=image|region, method, calibration_snapshot, score_spec)`

**Consumes:** S0-02, S3-03, S3-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- PaDiM/PatchCore/DINO 계열의 정상 학습·분류/영역 purpose·raw/calibrated score·heatmap·이미지/영역 metric을 구분한다.
- val/calibration에서 threshold를 고정하고 heldout test에서는 평가만 한다. 정상 데이터 없음과 reference-only synthetic score를 숨기지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s4_05.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s4_05.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S4-06 OCR 검출과 인식 recipe

**Files:**

- Modify: `backend/engine/ocr.py`
- Modify: `backend/api/routes_ocr.py`
- Modify: `src/renderer/components/training/OCRWorkbench.tsx`
- Test: `backend/tests/test_service_s4_06.py`

**Interfaces:** `OCRRecipe(mode=crop|detect_recognize, charset, normalizer, text_rules)`

**Consumes:** S1-05, S3-05, S3-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 현재 single-line crop 지원과 확장 multiline/문자영역 검출+인식을 지원표로 구분한다. unknown charset·한글/숫자·빈 text·세로 text 범위를 명시한다.
- train charset에 없는 val/test 문자를 preflight에서 처리한다. CER/WER·정규식/길이/허용값 규칙·원본좌표 viewer→flow→export를 검증한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s4_06.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s4_06.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S4-07 회전 검출 production adapter

**Files:**

- Modify: `backend/engine/rotated_detection.py`
- Modify: `backend/api/routes_rotated_detection.py`
- Modify: `src/renderer/components/training/RotatedDetectionPanel.tsx`
- Test: `backend/tests/test_service_s4_07.py`

**Interfaces:** `OBBRecipe(adapter, angle_convention, direction_schema, empty_background_policy)`

**Consumes:** S1-05, S3-05, S3-07, S6-01

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- YOLO OBB를 우선 후보로 검토하고 현재 fixed-slot CNN과 호환/전환 전략을 기록한다. 빈 정상 이미지·32 초과 object·axial angle와 direction 차이를 처리한다.
- OBB label→train→rotated IoU/mAP→ROI/정렬→flow→export를 검증한다. adapter와 모델 provenance·라이선스를 확정 전 임의 교체하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s4_07.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s4_07.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S4-08 회전과 정렬 recipe

**Files:**

- Modify: `backend/engine/rotation.py`
- Modify: `backend/api/routes_rotation.py`
- Modify: `src/renderer/components/training/RotationWorkbench.tsx`
- Test: `backend/tests/test_service_s4_08.py`

**Interfaces:** `AlignmentRecipe(method, angle_range, interpolation, coordinate_map)`

**Consumes:** S1-05, S3-05

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 회전 label·학습·angle error·정렬 적용·원본/파생좌표 역변환을 검증한다. 0/180° 및 axial/direction 구분을 표시한다.
- 모델 정렬과 기하/기준 template 정렬의 지원 범위를 구분하고 ROI/box/mask/text와 package 동작 일치를 확인한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s4_08.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s4_08.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S4-09 이미지 개선 실제 pair recipe

**Files:**

- Modify: `backend/engine/enhancement.py`
- Modify: `backend/api/routes_enhancement.py`
- Modify: `src/renderer/components/training/EnhancementWorkbench.tsx`
- Test: `backend/tests/test_service_s4_09.py`

**Interfaces:** `EnhancementRecipe(task, pair_mapping, preservation_policy)`

**Consumes:** S1-05, S3-08

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 실제 input-target pair import/정합·synthetic denoise 예제·누락/중복 pair 검사를 GUI로 제공한다.
- 학습·PSNR/SSIM 및 defect preservation 점검·원본과 개선본 비교·flow/export를 연결한다. 개선 이미지가 원본 검수 근거를 대체하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s4_09.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s4_09.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S4-10 GAN 생성과 사람 검토

**Files:**

- Modify: `backend/engine/defect_gan.py`
- Modify: `backend/api/routes_defect_gan.py`
- Modify: `src/renderer/components/training/DefectGANWorkbench.tsx`
- Modify: `src/renderer/services/ganWorkflow.ts`
- Test: `backend/tests/test_service_s4_10.py`

**Interfaces:** `GenerationRecipe / SyntheticCandidate / adopt_synthetic(train_snapshot)`

**Consumes:** S1-05, S3-04, S3-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 결함/background/위치/합성 mask·생성 설정·seed·parent source를 저장하고 생성→사람 검토→train-only adoption을 검증한다.
- GAN은 검사 노드와 구분한다. synthetic가 val/test에 섞이지 않고 실제 데이터 품질 또는 신규 정상 truth로 인정되지 않는다. 생성 package/추적 기록을 별도로 관리한다.
- 원본 이미지에 한 개/여러 개 합성 위치 지정·mask/box provenance를 유지하고 실제 heldout에서 합성 채택 전후 downstream 모델 A/B를 제공한다. 합성 자체 분포 score로 공정 품질을 승인하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s4_10.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s4_10.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S4-11 모든 작업의 실행 위치 통합

**Files:**

- Modify: `backend/engine/model_execution.py`
- Modify: `backend/engine/specialized_training_jobs.py`
- Modify: `backend/remote/operations.py`
- Modify: `src/renderer/services/modelExecution.ts`
- Modify: `backend/api/routes_evaluation.py`
- Modify: `backend/api/routes_export.py`
- Modify: `backend/engine/exporter.py`
- Test: `backend/tests/test_service_s4_11.py`

**Interfaces:** `execute_recipe(stage, recipe_ref, target_ref, input_snapshot) -> JobRef`

**Consumes:** S1-02, S1-05

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- manual train뿐 아니라 specialist evaluate/infer·flow·benchmark·export preflight에서 같은 target를 선택한다.
- 해당 target/device/runtime/hash 결과를 저장하며 선택 server가 안 되면 조용히 API host에서 실행하지 않는다. 모든 모델의 지원표를 검증한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s4_11.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s4_11.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S4-12 AutoDL과 checkpoint 재개

**Files:**

- Modify: `backend/api/routes_automated_training.py`
- Modify: `backend/engine/training_engine.py`
- Modify: `backend/engine/training_provenance.py`
- Modify: `src/renderer/components/training/AutoDLWorkbench.tsx`
- Test: `backend/tests/test_service_s4_12.py`

**Interfaces:** `SearchJob(parent, trials, budget, objective) / ResumeCheckpoint(model, optimizer, scheduler, scaler, rng, step)`

**Consumes:** S1-02, S1-03, S4-11

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 원격/로컬 trial를 같은 snapshot/seed/objective/budget에 묶고 진행·비용예산·취소·완료 trial 재사용을 제공한다.
- warm-start와 exact resume를 구분한다. optimizer/RNG/AMP/step 복원과 재개 불가능 recipe의 이유를 표시한다. DDP는 replica data-parallel로 정직하게 표시한다.
- quick·architecture/hyperparameter/augmentation 유한 탐색·fast retrain과 선택 latency objective의 실제 적용 controls를 공개한다. search중지·완료trial·best config 재사용을 검증한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s4_12.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s4_12.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S4-13 동일 cohort 모델과 전체 flow 평가

**Files:**

- Modify: `backend/api/routes_model_comparisons.py`
- Modify: `backend/engine/flow_evaluation.py`
- Modify: `src/renderer/components/flowchart/FlowEvaluationPanel.tsx`
- Test: `backend/tests/test_service_s4_13.py`

**Interfaces:** `EvaluationSpec(subject_ref, frozen_cohort, truth_binding, policy) -> EvaluationRef`

**Consumes:** S0-03, S2-06, S3-03, S3-07, S4-11

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 동일 테스트 cohort에서 모델 A/B·이미지 차이·class/제품/Lot 오류·threshold sweep·재평가 history를 제공한다. CLS/SEG/DET/OCR/OBB/회전/개선 metric을 task에 맞게 사용한다.
- 전체 flow의 OK/NG/REVIEW·미검/과검·node/ROI 근거·truth coverage와 unavailable metric을 기록한다. calibration split과 test split 역할을 지킨다.
- 서로 다른 task 모델은 동일 이미지의 공통 판정/오류 기준으로 비교한다. task 전용 metric의 직접 비교가 불가능하면 unavailable과 이유를 표시한다. Best/Important flag·부모 모델별 재평가·checkpoint/data/config/metric/prediction JSON export를 유지한다. threshold sweep/tuning의 기본 입력은 val/calibration이다. heldout test에서는 고정된 threshold/rules만 평가한다. exploratory test tuning을 하면 deployment-quality 근거에서 제외하고 새 untouched heldout을 요구한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s4_13.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s4_13.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S4-14 모델 및 전체 flow 승인

**Files:**

- Modify: `backend/api/routes_model_deployments.py`
- Modify: `backend/engine/runtime_release_evidence.py`
- Modify: `backend/engine/flow_package.py`
- Modify: `src/renderer/components/inference/FlowPackagePanel.tsx`
- Test: `backend/tests/test_service_s4_14.py`

**Interfaces:** `ApprovalRevision(subject=model|flow, evaluation_ref, policy_ref, reviewer) / ReleaseManifest`

**Consumes:** S0-04, S2-05, S4-13

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 모델·다중 모델·ROI·전처리·분기·merge·decision rules가 함께 포함된 전체 flow를 승인 대상으로 지정한다.
- 활성화/rollback·package parity·device acceptance·현재 승인 eligibility를 연결한다. graph나 threshold 변경은 새 평가/승인 요구로 표시하고 실행 패키지 구성과 checksum을 검증한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s4_14.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s4_14.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## phase 완료 판정

- 해당 task의 계약·영속·재열기·실패·handoff 근거가 존재한다.
- 새로운 interface가 다음 phase 및 legacy ID와 연결되어 있다.
- 테스트 skip/부족 데이터/미지원 target/서명/실장비 미검증은 이유와 owner를 남긴다.
- 조건이 없는 항목의 `not_required`는 사유와 reviewer가 있어야 하며 pending을 임의 완료로 바꾸지 않는다.

## 추가 기능 acceptance 연결

구현 시 spec 확장 계약과 registry의 acceptance를 함께 사용한다. 다른 phase의 owner가 만든 계약은 검토한 뒤 소비한다.

- E02 기준 특징에 고정되는 fixture ROI: owner S2-05; 이 phase 연결 S4-08
