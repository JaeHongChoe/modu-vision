# 데이터와 팀 검수 실행 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. 이 문서는 검토용이며 새로운 구현 완료 상태가 아니다.

**Goal:** 대규모 데이터의 출처, 라벨, 정답, 검수, 분할을 버전으로 연결한다.

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

## S3-01 데이터 index와 import

**Files:**

- Modify: `backend/api/routes_dataset.py`
- Create: `backend/engine/dataset_index.py`
- Modify: `backend/engine/annotation_formats.py`
- Test: `backend/tests/test_service_s3_01.py`

**Interfaces:** `ImportJob(manifest, mapping, mode) -> DatasetRevision / query_images(...)`

**Consumes:** S1-02, S1-06

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- LabelMe/COCO/YOLO·폴더/ZIP·mask·고해상도·지원 DICOM을 작업으로 가져오며 한글/공백/긴 경로/동명 파일·손상·중복·중단을 처리한다.
- 이미지 index·thumbnail·incremental hash·paged search를 저장한다. 지원하지 않는 format/channel/shape는 명시적 오류와 원본 유지로 처리한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s3_01.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s3_01.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S3-02 dataset snapshot과 출처

**Files:**

- Modify: `backend/engine/dataset_fingerprint.py`
- Modify: `backend/api/routes_dataset_versions.py`
- Modify: `backend/engine/training_provenance.py`
- Test: `backend/tests/test_service_s3_02.py`

**Interfaces:** `DatasetSnapshot(image_refs, annotation_revision, labelbook_revision, truth_revision, split_revision)`

**Consumes:** S1-06, S1-08

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 이미지·라벨·분할·모델·flow·검사 결과의 ID와 revision/hash 관계를 한 화면에서 추적한다.
- 원본은 쓰기 금지로 유지하고 다른 사람 편집/외부파일 변화는 stale로 감지한다. runtime/eval 입력 snapshot은 UI 현재선택과 분리한다.
- DatasetSnapshot에 guideline/policy/review revision과 eligible cohort binding을 포함한다. 승인 철회는 label bytes가 같아도 준비도와 학습 적격성을 갱신한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s3_02.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s3_02.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S3-03 태그와 명시적 정답

**Files:**

- Modify: `backend/api/routes_dataset_metadata.py`
- Modify: `backend/api/routes_image_truth.py`
- Modify: `src/renderer/components/labeling/ImageReviewPanel.tsx`
- Test: `backend/tests/test_service_s3_03.py`

**Interfaces:** `ImageMetadata(tags, product, lot, source) / TruthRecord(task, class_roles, revision, reviewer)`

**Consumes:** S0-03, S3-02

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 미작업/검수필요/승인·검토자·수정이력을 저장하고 OK/NG/UNKNOWN 정답을 학습 label과 구분한다.
- 빈 annotation을 OK로 만들지 않는다. evaluation 화면에서 정상 정답 검토 화면에 직접 이동하고 변경 후 영향받는 승인/평가를 표시한다.
- 여러 이름 있는 label set·set별 flag·이미지별 color tag·작업완료 통계·class/split 분포를 저장하고 해당 통계에서 실제 image filter로 이동한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s3_03.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s3_03.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S3-04 팀 라벨 편집과 충돌 처리

**Files:**

- Modify: `backend/engine/annotation_transactions.py`
- Modify: `backend/engine/annotation_storage.py`
- Modify: `src/renderer/components/labeling/TeamDataPanel.tsx`
- Test: `backend/tests/test_service_s3_04.py`

**Interfaces:** `save_annotation(expected_revision, lock_token, changes) -> AnnotationRevision`

**Consumes:** S1-07, S3-02

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 이미지 작업 잠금·만료·낙관적 충돌·검토 guideline 버전·라벨 합의·승인 권한을 연결한다.
- 동시 저장이 다른 사람 결과를 덮지 않는다. offline draft 재연결 충돌은 비교 후 명시적 적용을 사용한다.
- 선택 팀 정책은 서로 다른 두 reviewer, self-review 금지 옵션, image/annotation/mask/guideline/policy revision에 묶인 vote와 불일치 adjudication/사유를 가진다. 수정 후 vote 무효화와 직접 metadata edit 우회를 차단한다. 배정자/구성원/우선순위/검수 작업 queue와 재개를 유지한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s3_04.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s3_04.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S3-05 라벨 도구 기능과 format 왕복

**Files:**

- Modify: `src/renderer/components/labeling/CanvasToolbar.tsx`
- Modify: `src/renderer/components/labeling/BrushTool.tsx`
- Modify: `src/renderer/components/labeling/PolygonTool.tsx`
- Modify: `backend/engine/annotation_formats.py`
- Test: `backend/tests/test_service_s3_05.py`

**Interfaces:** `AnnotationSchema(bbox, polygon, mask, obb, direction, text) / export_dataset(...)`

**Consumes:** S3-01, S3-04

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- bbox·polygon·brush/eraser·mask layer·zoom/pan·Undo/Redo·OBB·direction·OCR text 각각 실제 클릭과 저장/재열기를 검증한다.
- LabelMe/COCO/YOLO·mask 입출력에서 클래스·좌표·shape·이미지 mapping을 왕복 비교한다. 손실 format은 사전 경고와 provenance를 남긴다.
- OBB 중심대칭/한쪽면/비정형 fitting과 별도 direction target를 유지한다. label set별 모델 결과 선택과 원본 위 groundtruth/prediction 구분을 검증한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s3_05.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s3_05.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S3-06 모델과 프롬프트 라벨 제안

**Files:**

- Modify: `backend/engine/label_candidate_providers.py`
- Modify: `backend/engine/foundation_labeling.py`
- Modify: `src/renderer/components/labeling/ModelAssistPanel.tsx`
- Modify: `src/renderer/components/labeling/KoreanConditionLabeler.tsx`
- Test: `backend/tests/test_service_s3_06.py`

**Interfaces:** `propose_labels(snapshot, provider_spec) -> CandidateSet / review_candidates(...)`

**Consumes:** S1-05, S3-04, S3-05

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 기존 모델 일괄 제안→사람 검토→승인과 point/box/text/image prompt·한국어 keyword 조건·예제 비교를 후보 상태로 연결한다.
- 실제 provider/device/weight hash·오류·취소·재시작을 기록한다. 외부 provider는 opt-in이며 데이터 전송 범위·비밀정보 보관을 표시한다. 자동 승인과 무단 원본 변경을 하지 않는다.
- positive/negative image prompt·제외영역·긴 text 결합·box→polygon·작은 수동 label 기반 추천기 갱신·candidate size와 CPU/GPU filter를 유지한다. synthetic/provider 결과는 review 상태를 거친다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s3_06.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s3_06.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S3-07 제품과 Lot 분할 및 준비도

**Files:**

- Modify: `backend/engine/heldout_splits.py`
- Modify: `backend/engine/dataset_summary.py`
- Modify: `src/renderer/components/dataset/DataReadinessPanel.tsx`
- Test: `backend/tests/test_service_s3_07.py`

**Interfaces:** `build_split(snapshot, grouping, seed) -> SplitRevision / readiness(snapshot, recipe)`

**Consumes:** S3-01, S3-02, S3-03

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- Lot/제품/원본유래 group 단위 split, near duplicate·blur·exposure·label coverage·class balance·task-specific schema를 검사한다.
- 같은 원본 crop/derived/synthetic이 train과 val/test에 새지 않는다. heldout 부족은 unavailable이며 seed·grouping·검사결과를 고정한다.
- approved-only 정책을 켜면 primary/specialist/prepared 실제 loader가 eligible cohort로만 학습한다. job 제출 시 review/policy/eligibility snapshot receipt를 고정하고 라벨이나 guideline 변경 후 stale를 검증한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s3_07.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s3_07.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S3-08 비파괴 편집과 재학습 영향

**Files:**

- Modify: `backend/engine/annotation_transactions.py`
- Modify: `backend/engine/workflow_impact.py`
- Modify: `src/renderer/components/labeling/DerivedImagePanel.tsx`
- Test: `backend/tests/test_service_s3_08.py`

**Interfaces:** `derive_image(source_ref, transform_spec) -> DerivedImageRef / impact(changed_refs)`

**Consumes:** S3-02, S3-05, S3-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- crop/rotate/align/brightness 등은 새 파생본과 annotation coordinate transform을 저장한다. 지원 불명 shape는 거절한다.
- edit→label review→새 snapshot→재학습→동일 cohort 비교→재승인 연결과 되돌리기를 검증한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s3_08.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s3_08.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S3-09 검사 수집과 active learning

**Files:**

- Modify: `backend/engine/capture_intake.py`
- Modify: `src/renderer/components/dataset/CaptureIntakePanel.tsx`
- Modify: `src/renderer/components/labeling/SavedReviewQueuePanel.tsx`
- Test: `backend/tests/test_service_s3_09.py`

**Interfaces:** `IntakeCandidate(run_ref, source_ref, reason) / adopt_intake(ids, target_revision)`

**Consumes:** S3-03, S3-04, S5-01

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 현장 REVIEW·모델 불일치·임계 근처·오류 이미지가 원래 run/node와 연결된 검수 queue로 들어온다.
- 중복·손상·UNKNOWN routing·review progress·재개·원본으로 복귀·명시적 adoption을 검증한다. 현장 수집 데이터가 자동 test truth가 되지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s3_09.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s3_09.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S3-10 프로젝트 수명과 데이터 이동

**Files:**

- Modify: `backend/engine/project_archive.py`
- Modify: `backend/engine/project_migration.py`
- Modify: `backend/api/routes_project.py`
- Test: `backend/tests/test_service_s3_10.py`

**Interfaces:** `archive_project(ref) -> ArchiveManifest / restore_project(...)`

**Consumes:** S1-08, S3-02

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- export/import/project template·soft delete/trash/restore·보존기한·quota·버전별 archive를 제공한다.
- 다른 서버로 프로젝트를 옮겨도 상대 ID와 hash로 재연결한다. 원본·결과·승인 기록 누락과 비밀정보 포함을 검사한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s3_10.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s3_10.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## phase 완료 판정

- 해당 task의 계약·영속·재열기·실패·handoff 근거가 존재한다.
- 새로운 interface가 다음 phase 및 legacy ID와 연결되어 있다.
- 테스트 skip/부족 데이터/미지원 target/서명/실장비 미검증은 이유와 owner를 남긴다.
- 조건이 없는 항목의 `not_required`는 사유와 reviewer가 있어야 하며 pending을 임의 완료로 바꾸지 않는다.

## 추가 기능 acceptance 연결

구현 시 spec 확장 계약과 registry의 acceptance를 함께 사용한다. 다른 phase의 owner가 만든 계약은 검토한 뒤 소비한다.

- E03 실제 치수 교정 artifact와 설정 일치 검사: owner S3-08; 이 phase 연결 S3-08
- E05 정답 기준 라벨 검수와 불일치 위치 표시: owner S3-07; 이 phase 연결 S3-04, S3-07
- E06 현장 수집량을 제한하는 sample 정책: owner S5-08; 이 phase 연결 S3-09
- E08 데이터 입출력·백업도 같은 영속 작업 센터에서 재개: owner S1-02; 이 phase 연결 S3-01, S3-10
