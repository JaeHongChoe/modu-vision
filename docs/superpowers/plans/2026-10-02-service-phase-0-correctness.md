# 판정과 승인 무결성 실행 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. 이 문서는 검토용이며 새로운 구현 완료 상태가 아니다.

**Goal:** 재현된 결함을 먼저 해결하고 영향받은 과거 결과를 식별한다.

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

## S0-01 공간 증강의 이미지와 라벨 동시 변환

**Files:**

- Modify: `backend/engine/augmentations.py`
- Modify: `backend/engine/dataset_loaders.py`
- Modify: `backend/engine/grouped_dataset_views.py`
- Modify: `backend/engine/trainer.py`
- Test: `backend/tests/test_service_s0_01.py`

**Interfaces:** `augment_sample(image, targets, task, seed) -> AugmentedSample(image, targets, transform_receipt)`

**Consumes:** 현재 baseline source와 위 설계의 공통 계약

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 실제 Dataset.__getitem__에서 32px 수평 반전 bbox [2,3,6,7]→[26,3,30,7], 이미지와 마스크 IoU=1.0을 검증한다.
- 반전·회전·crop과 mask nearest 보간·박스 clipping·빈 객체·none/photometric을 production loader 경로에서 검증한다. 회전 박스와 direction도 변환 규칙을 가진다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s0_01.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s0_01.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S0-02 이상 점수와 임계값의 단위 통일

**Files:**

- Modify: `backend/engine/anomaly/padim.py`
- Modify: `backend/engine/anomaly/patchcore.py`
- Modify: `backend/engine/flowchart_engine.py`
- Modify: `src/renderer/components/flowchart/modelFlowHandoff.ts`
- Modify: `src/renderer/components/flowchart/FlowchartStudio.tsx`
- Test: `backend/tests/test_service_s0_02.py`

**Interfaces:** `ScoreSpec(domain, unit, direction, calibration_id, threshold) / resolve_decision(score, spec)`

**Consumes:** S0-09

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- raw score=4, 모델 threshold=8인 경우 평가·플로우·패키지에서 모두 OK이다. 확률 모델과 원시 거리 모델을 혼합해 검증한다.
- 0~1 밖 거리 임계값을 저장·재열기·전달하며, calibration/score-domain 불일치는 실행 전에 거절한다. 임계값 선택에는 test 데이터를 사용하지 않는다.
- 단위를 표시하는 숫자 입력과 domain별 범위 검증을 UI에 적용한다. threshold=8을 사용자가 편집·저장·재열기·실행해도 .95 이하 slider 값으로 덮지 않는다. 실제 renderer/Electron 회귀를 S0-09 환경에서 확인한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s0_02.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s0_02.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S0-03 변경된 정답과 오래된 승인 차단

**Files:**

- Modify: `backend/api/routes_model_deployments.py`
- Modify: `backend/engine/comparison_truth.py`
- Modify: `backend/engine/image_truth.py`
- Modify: `backend/engine/dataset_fingerprint.py`
- Modify: `backend/api/routes_export.py`
- Test: `backend/tests/test_service_s0_03.py`

**Interfaces:** `verify_evidence_binding(binding, current_context) -> EligibilityResult`

**Consumes:** 현재 baseline source와 위 설계의 공통 계약

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 비교 후 OK→UNKNOWN, 라벨·원본·split·class-role 변경에서 approve와 새로운 export/stage가 모두 명시적 stale 이유로 거절된다.
- 평가 생성 당시 truth_binding과 현행 truth revision/hash를 비교한다. 승인 시점부터 export까지 변경되는 경쟁 조건도 고정된 revision 또는 transaction 검증으로 막는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s0_03.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s0_03.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S0-04 중앙 배포와 rollback의 공통 적격성 검사

**Files:**

- Modify: `backend/api/routes_fleet.py`
- Modify: `backend/engine/fleet.py`
- Modify: `backend/engine/managed_service.py`
- Modify: `backend/engine/runtime_release_evidence.py`
- Test: `backend/tests/test_service_s0_04.py`

**Interfaces:** `authorize_release_action(release_ref, action, context) -> ReleaseEligibility`

**Consumes:** 현재 baseline source와 위 설계의 공통 계약

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 철회된 revision으로 중앙 rollback 요청 시 remote /releases·/apply가 전송되지 않는다. 유효한 과거 승인 release는 정책 범위 안에서 복귀한다.
- 새 중앙 명령의 현재 승인 검사와 기존 offline runtime의 봉인된 정책 검증을 구분한다. 연결 실패·철회·device mismatch를 기록한다. 비상 복귀는 별도 권한과 사유로 기록한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s0_04.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s0_04.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S0-05 플로우 실행과 화면 상태 결함 수정

**Files:**

- Modify: `src/renderer/components/flowchart/FlowEvaluationPanel.tsx`
- Modify: `src/renderer/components/flowchart/FlowchartStudio.tsx`
- Modify: `src/renderer/stores/useFlowchartStore.ts`
- Modify: `src/renderer/components/training/TrainingController.tsx`
- Modify: `src/renderer/stores/useProjectStore.ts`
- Test: `scripts/e2e/service-s0-05.spec.ts`

**Interfaces:** `FlowIdentity(semantic_revision, layout_revision) / updateTask(...) -> explicit success-or-error`

**Consumes:** S0-02, S0-09

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 실행 중 노드 위치 이동으로 완료 결과가 사라지지 않는다. semantic 변경은 이전 결과를 올바르게 다른 버전으로 보존한다.
- 평가 버전 변경 후 이전 결과에 새 버전의 유효 표시를 붙이지 않는다. 실패한 task 변경은 프로젝트와 모델 선택 화면을 일치시킨다. 오류 이미지 열기는 실제 viewer로 이동한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s0-05.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s0-05.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S0-06 복구와 보안 후보의 재현 및 판정

**Files:**

- Modify: `backend/engine/local_training_worker.py`
- Modify: `backend/engine/service_bootstrap.py`
- Modify: `backend/engine/inspection_service.py`
- Modify: `src/main/supervisor.ts`
- Modify: `src/main/index.ts`
- Test: `backend/tests/test_service_s0_06.py`

**Interfaces:** `AuditCase(candidate, setup, observed, status, remediation)`

**Consumes:** 현재 baseline source와 위 설계의 공통 계약

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 종료 작업 receipt/PID 재사용/불확실 lease, frozen 자동 작업 entrypoint, 검사 실패 재시도, 공유 HTTPS/CSP를 격리 fixture에서 판정한다.
- checkpoint import, Electron IPC/sandbox, external URL과 파일 경로 접근의 실제 입력 경로를 조사한다. 후보를 확인 전 결함으로 표기하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s0_06.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s0_06.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S0-07 기존 모델과 승인 결과의 영향 조사

**Files:**

- Modify: `backend/engine/training_provenance.py`
- Modify: `backend/engine/workflow_impact.py`
- Modify: `backend/api/routes_model_operations.py`
- Test: `backend/tests/test_service_s0_07.py`

**Interfaces:** `assess_legacy_impact(snapshot) -> ImpactReport(affected, unaffected, unknown, next_action)`

**Consumes:** S0-01, S0-02, S0-03, S0-04

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 공간 증강 설정을 근거로 영향을 받은 모델과 설정 불명 모델을 구분한다. 기존 파일을 삭제하거나 전체 모델을 일괄 폐기하지 않는다.
- 재평가/재학습/재승인 대상과 이유를 표시한다. 품질 저하 원인은 같은 데이터·설정의 비교 결과가 있을 때만 결론 낸다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s0_07.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s0_07.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S0-08 핵심 결함 회귀와 기준 기록

**Files:**

- Create: `backend/tests/test_service_integrity.py`
- Create: `scripts/e2e/service-integrity.spec.ts`
- Create: `docs/implementation-ledger/SERVICE-UPGRADE.md`
- Test: `backend/tests/test_service_s0_08.py`

**Interfaces:** `BaselineEvidence(source_sha, fixture_hash, path, command, result, scope)`

**Consumes:** S0-01, S0-02, S0-03, S0-04, S0-05, S0-09

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 위 네 재현 결함을 helper 단독 테스트가 아닌 loader/API/export/중앙 배포 경로로 고정한다.
- 실제 UI에서 평가→플로우 임계값 전달, 버전 변경, task 변경 실패를 확인하고 코드 검사와 실제 클릭 검증을 구분해 기록한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s0_08.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s0_08.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S0-09 초기 UI와 Electron 검증 환경

**Files:**

- Modify: `package.json`
- Modify: `package-lock.json`
- Create: `playwright.config.ts`
- Create: `scripts/e2e/fixtures`
- Create: `scripts/tests/e2e-harness.test.cjs`
- Test: `scripts/tests/e2e-harness.test.cjs`

**Interfaces:** `E2EHarness(mode=browser|electron, isolated_project, owned_backend) -> HarnessReceipt`

**Consumes:** 현재 baseline source와 위 설계의 공통 계약

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- Node builtin bootstrap test로 누락 dependency/config를 먼저 검증하고 package lock과 E2E setup을 task가 소유한다. browser renderer와 Electron actual window fixture를 분리한다.
- 임시 프로젝트·자신이 시작한 backend·고정 port/health·artifact/screenshot 저장·소유 process cleanup을 검증한다. 실제 Windows runner와 native 설치 증거는 별도 gate다. 원본 데이터·기존 앱·타 process를 건드리지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `node --test scripts/tests/e2e-harness.test.cjs`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `node --test scripts/tests/e2e-harness.test.cjs`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## phase 완료 판정

- 해당 task의 계약·영속·재열기·실패·handoff 근거가 존재한다.
- 새로운 interface가 다음 phase 및 legacy ID와 연결되어 있다.
- 테스트 skip/부족 데이터/미지원 target/서명/실장비 미검증은 이유와 owner를 남긴다.
- 조건이 없는 항목의 `not_required`는 사유와 reviewer가 있어야 하며 pending을 임의 완료로 바꾸지 않는다.
