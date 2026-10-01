# 실사용 검증과 공개 릴리스 실행 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. 이 문서는 검토용이며 새로운 구현 완료 상태가 아니다.

**Goal:** 기능 수 대신 실제 Windows 사용과 복구 증거로 릴리스를 판정한다.

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

## S7-01 기존 기능 전체 coverage 계약

**Files:**

- Modify: `docs/service-upgrade-program.json`
- Modify: `docs/service-upgrade-coverage.md`
- Modify after S6-05 handoff: `scripts/check_service_plan.py`
- Modify after S0-08 handoff: `docs/implementation-ledger/SERVICE-UPGRADE.md`
- Test: `scripts/e2e/service-s7-01.spec.ts`

**Interfaces:** `RequirementEvidence(id, implementation, gui, persist, reopen, failure, handoff, platform, target, prerequisites)`

**Consumes:** S6-05, S0-08

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- F001–F123/U001–U033의 모든 ID를 새 계획 owner와 연결한다. 기존 integration 표시를 실사용 accepted로 승격하지 않는다.
- 버튼/메뉴/shortcut/action 목록에 성공·empty·invalid·error·cancel·reopen·handoff 근거를 기록한다. 미실행 및 skip 이유는 pending/not_required 근거로 표시한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s7-01.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s7-01.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S7-02 10모델군 실제 작업 시나리오

**Files:**

- Extend directory: `backend/tests`
- Create: `scripts/e2e/model-lifecycle.spec.ts`
- Extend directory: `docs/implementation-ledger`
- Test: `scripts/e2e/service-s7-02.spec.ts`

**Interfaces:** `LifecycleReceipt(family, dataset, labels, target, train, eval, flow_or_adoption, export, hashes)`

**Consumes:** S3-05, S4-01, S4-02, S4-03, S4-04, S4-05, S4-06, S4-07, S4-08, S4-09, S4-10, S4-14

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 각 모델의 적합한 사람이 검토한 label fixture와 실제 input으로 준비→학습→평가→flow/채택→package를 수행한다. 제공 데이터는 원본 보존 복사본/manifest로 사용한다.
- 모든 task를 똑같은 NG 폴더로 억지 검증하지 않는다. 정상 truth/OCRtext/OBBangle/enhancementpair/GANreview 부족은 explicit prerequisite로 남긴다.
- 한 chain에서 서로 다른 다섯 checkpoint와 반복/shared model을 연결한 검사도 준비된 checkpoint가 있을 때 실제 실행한다. ROI→SEG+Patch→aggregate·회전→OCR 등의 대표 multi-model recipe를 package까지 검증한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s7-02.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s7-02.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S7-03 Windows 실제 설치와 사용 QA

**Files:**

- Create: `scripts/e2e/windows-studio.spec.ts`
- Modify: `scripts/release_backend_acceptance.py`
- Extend directory: `docs/implementation-ledger`
- Test: `scripts/e2e/service-s7-03.spec.ts`

**Interfaces:** `WindowsAcceptance(os_build, installed_artifact, actor, screen_proof, result_receipts)`

**Consumes:** S2-08, S6-02, S6-04, S6-08

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 실제 Windows11x64 VM/장비에서 NSIS 설치·CPUknown-image·전체UI버튼·DPI·한글경로·업데이트·제거·재설치를 검증한다.
- NVIDIA가 있는 target의 localGPU와 Windows→Linuxremote 실행은 별도 receipt로 남긴다. macOS 결과로 Windows gate를 대신하지 않는다.
- 공개 후보 이전에 처음 사용하는 사람이 설명 없이 demo→자기 데이터→학습→평가→flow→검사/오류검수를 수행하는 usability pilot을 기록한다. blocker와 잘못된 OK/버전 안내는 공개 전에 수정하고 같은 경로를 재검증한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s7-03.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s7-03.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S7-04 팀 동시 작업과 fault injection

**Files:**

- Create: `backend/tests/test_service_failure_matrix.py`
- Create: `scripts/e2e/team-recovery.spec.ts`
- Test: `scripts/e2e/service-s7-04.spec.ts`

**Interfaces:** `FailureMatrix(mode, stage, failure_point, expected_state, observed_state)`

**Consumes:** S1-08, S5-06, S5-10, S6-04

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 두 사용자 project/label 충돌·role 철회·samejob submit·API/worker crash·네트워크중단·PID재사용·diskfull·OOM·취소 race를 검증한다.
- 승인→export→중앙apply/rollback 사이 정답·권한·버전 변경과 partial transfer/update를 검증한다. 타 작업과 예약은 보존한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s7-04.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s7-04.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S7-05 데이터 규모와 연속 운전

**Files:**

- Create: `scripts/acceptance/scale_and_soak.py`
- Extend directory: `docs/implementation-ledger`
- Test: `scripts/e2e/service-s7-05.spec.ts`

**Interfaces:** `CapacityReceipt(images, pixels, users, workers, queue_rate, duration, resources, errors)`

**Consumes:** S3-01, S5-02, S5-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 10k/100k 이미지 metadata 검색 fixture와 큰 원본 image를 별도로 검증한다. 목표 장비의 RAM/disk/p95목록반응/queue 용량을 실제 측정한다.
- 72시간 연속 검사와 backlog·중복전송·reconnect·diskquota를 검증한다. 이 값은 계획 목표이며 실제지원한계/택트성능은 측정 후 공개한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s7-05.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s7-05.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S7-06 공정 품질 승인과 장비 검증

**Files:**

- Create: `docs/implementation-ledger/QUALITY-ACCEPTANCE.md`
- Create: `docs/implementation-ledger/FIELD-ACCEPTANCE.md`
- Test: `scripts/e2e/service-s7-06.spec.ts`

**Interfaces:** `QualityApproval(task, reviewed_cohort, escape_limit, overkill_limit, reviewer) / HardwareReceipt`

**Consumes:** S4-13, S5-03, S5-04, S7-02

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 정상/불량·제품/Lot별 검토 cohort와 사용자가 정한 미검/과검 정책으로 품질을 별도 승인한다. 공정마다 다른 기준을 숨기지 않는다.
- 카메라/PLC/MES 실제장비 없는 경우 simulator contract까지만 완료이다. 산업별 성능 승인과 기능 출시 상태를 각각 공개한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s7-06.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s7-06.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S7-07 공개 후보와 릴리스 판정

**Files:**

- Create: `docs/release-checklist.md`
- Modify after S0-08 handoff: `docs/implementation-ledger/SERVICE-UPGRADE.md`
- Modify: `docs/service-upgrade-program.json`
- Test: `scripts/e2e/service-s7-07.spec.ts`

**Interfaces:** `ReleaseDecision(source, artifacts, coverage, known_issues, license, sign, target_acceptance)`

**Consumes:** S7-01, S7-03, S7-04, S7-05, S6-06, S6-09, S7-08, S6-11, S0-08

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- critical defects=0, 공개된 supported 조합은 실제 target기능 증거를 가지며 stale 승인·판정 불일치·데이터유실·무단접근 회귀를 통과한다.
- 라이선스·Windows native install·upgrade/restore·문서·지원표·source/SBOM·서명 상태와 known issues를 공개한다. 외부 조건 pending을 완료로 바꾸지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s7-07.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s7-07.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S7-08 파일럿 feedback과 지속 유지

**Files:**

- Create: `docs/pilot-playbook.md`
- Create: `docs/maintainer-release-policy.md`
- Modify after S6-07 handoff: `.github/ISSUE_TEMPLATE`
- Test: `scripts/e2e/service-s7-08.spec.ts`

**Interfaces:** `PilotReview(critical_incidents, workflow_completion, user_feedback, next_release)`

**Consumes:** S7-03, S7-04, S6-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 설명 없이 사용자가 데이터→학습→평가→flow→검사/오류검수를 수행하는 파일럿을 기록하고 막힌 동선과 운영 장애를 다음 release에 반영한다.
- 지원/보안/회귀 triage·patch release·schema/protocol deprecation·backup/restore 연습 주기를 운영한다. 서비스 SLA는 실제 측정과 운영 책임이 정해진 뒤 약속한다.
- 첫 usability pilot은 public release 전에 수행하며 결과와 critical blocker 해결을 S7-07의 입력으로 제공한다. 이후 유지보수 feedback은 반복 release 운영으로 이어간다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s7-08.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s7-08.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## phase 완료 판정

- 해당 task의 계약·영속·재열기·실패·handoff 근거가 존재한다.
- 새로운 interface가 다음 phase 및 legacy ID와 연결되어 있다.
- 테스트 skip/부족 데이터/미지원 target/서명/실장비 미검증은 이유와 owner를 남긴다.
- 조건이 없는 항목의 `not_required`는 사유와 reviewer가 있어야 하며 pending을 임의 완료로 바꾸지 않는다.
