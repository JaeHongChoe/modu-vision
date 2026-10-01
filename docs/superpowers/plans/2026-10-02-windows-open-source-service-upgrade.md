# Windows 오픈소스 서비스 고도화 실행 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. 상태는 검토용 초안이다. 이번 작성은 제품 코드를 수정하거나 릴리스를 발행하지 않는다.

**Goal:** 개인과 팀이 Windows에서 데이터부터 검사 운영까지 사용할 수 있는 오픈소스 제품으로 고도화한다.

**Architecture:** Windows local-first Studio, 공통 modular API와 durable jobs, 선택 팀 서버/Linux worker, 독립 inspection runtime. 현재 엔진과 데이터 출처 계약을 이어간다.

**Tech Stack:** React/TypeScript/Electron, Python/FastAPI/PyTorch, SQLite와 storage/DB adapter.

**Spec:** ../specs/2026-10-02-windows-open-source-service-design.md

## 한눈에 보는 범위

82개 작업 묶음은 현재 기능 완결과 서비스 기능 추가를 함께 포함한다. 기존 F123/U33 항목을 중복 새 기능 수로 계산하지 않는다. 전체 매핑은 `docs/service-upgrade-coverage.md`, 기계 판독 상태는 `docs/service-upgrade-program.json`을 사용한다.

| 차수 | 범위 | 작업 | 결과 |
| --- | --- | --- | --- |
| 0 | [판정과 승인 무결성](2026-10-02-service-phase-0-correctness.md) | 9 | 재현된 결함을 먼저 해결하고 영향받은 과거 결과를 식별한다. |
| 1 | [Windows와 서버 공통 기반](2026-10-02-service-phase-1-platform.md) | 10 | 로컬 및 팀 모드에서 같은 영속 작업·버전·권한 계약을 사용한다. |
| 2 | [사용자 화면과 5단계 플로우](2026-10-02-service-phase-2-studio.md) | 10 | 처음 사용하는 사람도 검사 플로우를 만들고 오류 원본을 확인할 수 있게 한다. |
| 3 | [데이터와 팀 검수](2026-10-02-service-phase-3-data.md) | 10 | 대규모 데이터의 출처, 라벨, 정답, 검수, 분할을 버전으로 연결한다. |
| 4 | [전체 모델 실행과 평가](2026-10-02-service-phase-4-models.md) | 14 | 10개 모델군의 준비·학습·평가·활용·패키징을 동일한 작업 방식으로 연결한다. |
| 5 | [검사 서비스와 현장 운영](2026-10-02-service-phase-5-operations.md) | 10 | 작업 화면 종료와 네트워크 장애에도 검사·결과 보존·전송·복구를 지속한다. |
| 6 | [Windows 오픈소스 배포](2026-10-02-service-phase-6-opensource.md) | 11 | 외부 사용자가 개발 환경 또는 설치 프로그램으로 재현할 수 있게 한다. |
| 7 | [실사용 검증과 공개 릴리스](2026-10-02-service-phase-7-acceptance.md) | 8 | 기능 수 대신 실제 Windows 사용과 복구 증거로 릴리스를 판정한다. |

## 실행 순서와 병렬 작업

1. S0-09로 UI 검증 환경을 준비하고 S0-01–08로 판정/승인 결함과 기존 결과 영향부터 확인·수정한다.
2. S1 공통 문맥·영속 작업·지원표·Windows process를 확정한다. S6 license/native build/CI discovery를 동시에 진행한다.
3. UX 담당 S2, data 담당 S3, model 담당 S4가 계약과 파일 ownership을 공유하고 병렬 진행한다. S2-07은 S3-01의 paged query에 의존한다.
4. S5 runtime/adapter/fleet/ops와 S6 installer/offline/SDK/docs를 연결한다. 이 단계에서도 field 조건이 없는 adapter는 simulator 수준을 분명히 표시한다.
5. S7 기능별 실제 Windows acceptance, fault/scale/soak, 공개 릴리스와 별도 field/quality gate를 기록한다.

## 공통 완료 규칙

- 구현과 계약 확인, 실제 UI, 저장, 재시작, 실패/취소 복구, 다음 단계 전달은 서로 다른 근거다.
- model quality·signing·Windows target 실행·실장비 acceptance는 조건별 gate다. 구현 체크로 자동 통과하지 않는다.
- 이미지/라벨/모델/flow/package/result/source SHA와 실행 target/device를 evidence에 묶는다.
- 공개 release 지원표의 조합은 실제 execution 근거가 필요하다. 지원 불가/미검증 조합은 감추지 않는다.

## 첫 착수 묶음

- **무결성 담당:** S0-01/02, production loader와 runtime/export score 계약.
- **승인 담당:** S0-03/04, truth freshness와 중앙 rollback eligibility.
- **UI 담당:** S0-05 및 S2-02/04/06 설계, 정확한 버전/원본 viewer/실행 상태.
- **통합 담당:** S0-06/07/08, 계약과 범위 검토, Windows target/license/CI discovery S6-01/02/05.

4개 역할은 실제 참여 인원과 도구에 따라 조정한다. API/router/schema/global store 변경은 통합 owner가 조율하고 implementer와 reviewer를 분리한다. 역할별 file handoff와 review 결과를 기록해 동시에 같은 파일을 덮지 않는다.

## 검토 및 integration

- phase별 작은 task에서 failing behavior test→최소 구현→focused 검증→독립 review를 수행한다.
- 공통 interface 변경 시 소비하는 phase의 contract test를 함께 실행한다. UI 검증은 renderer isolated assertion만으로 완료하지 않는다.
- milestone 경계에서는 필요한 broad gate를 한 번 수행하고 새 source/lock/receipt inventory를 남긴다. 문제 없는 범위를 반복 실행하여 완료를 지연하지 않는다.
- source doc와 task 상태를 구현 commit에 연결한다. 기존 성공 ledger는 역사로 보존하고 새 baseline의 상태를 별도로 관리한다.

## migration과 되돌리기

- 기존 project/JSON/model/flow/results를 backup하고 dry-run diff 후 새 storage로 cutover한다.
- identity·count·hash·revision·approval 상태를 비교한다. old model/flow를 무심코 최신 승인 대상으로 옮기지 않는다.
- 각 checkpoint에서 설치·DB schema·worker protocol·runtime package compatibility를 확인한다. 롤백 불가 migration은 복구 절차와 rehearsal이 필요하다.
- 새 버전 도입 중 원본과 기존 설치를 보존하고 candidate runtime의 확인 후 active pointer를 바꾼다.

## 공개에 앞서 필요한 결정

- 현재 MIT와 포함 모델/dependency의 배포 조건에 맞는 최종 license 정책. license 파일은 이번 계획에서 바꾸지 않는다.
- 실제 Windows target와 signing identity, Linux worker 실행 profile와 검증 비용 범위.
- task별 검토 label fixture 및 공정별 OK/NG heldout. 제공 NG 데이터만으로 모든 task/품질을 승인하지 않는다.
- 최초 team 사용 규모, backup/retention 책임, 실제 PLC/MES/camera가 생기면 사용할 acceptance 계약.

## 일정과 출시 명칭

초반 discovery에서 Windows build/license/기존 migration 위험을 확인한 뒤 task별 소요와 병렬 인력을 산정한다. 큰 개발 규모를 허용한 요청은 목표 범위를 줄이는 근거가 아니며, 날짜·SLA·정확도를 현재 증거 없이 약속하지 않는다. `2.0`은 gate 통과 후 붙일 후보 제품 버전 이름이며 현재 package version을 변경하지 않는다.

최초 CPU Windows alpha→team beta→service candidate→public release로 검토한다. 특정 공정 field approval은 정상/불량 정답과 장비 증거로 별도 판정한다.


## 차수 사이의 실제 의존 관계

차수 번호만으로 병렬 실행을 결정하지 않는다. 다음 계약을 먼저 완성하고 consumer를 이어간다. 아래 목록과 JSON의 task별 depends_on이 일정의 기준이다.

| consumer | 다른 차수의 선행 task |
| --- | --- |
| S1-01 프로젝트와 실행 문맥 공통 계약 | S0-08 |
| S2-01 첫 실행과 GPU 없는 예제 | S1-05, S1-09 |
| S2-03 6단계 진행과 준비도 안내 | S1-01, S0-05 |
| S2-05 typed DAG와 검사 규칙 편집 | S0-02, S1-05 |
| S2-06 공통 원본과 판정 viewer | S1-06 |
| S2-07 대용량 이미지 선택과 검색 | S1-06, S3-01 |
| S2-09 학습과 작업 센터 공통 화면 | S1-04, S1-05 |
| S2-10 운영자 화면과 개인 설정 | S1-07, S5-01 |
| S3-01 데이터 index와 import | S1-02, S1-06 |
| S3-02 dataset snapshot과 출처 | S1-06, S1-08 |
| S3-03 태그와 명시적 정답 | S0-03 |
| S3-04 팀 라벨 편집과 충돌 처리 | S1-07 |
| S3-06 모델과 프롬프트 라벨 제안 | S1-05 |
| S3-09 검사 수집과 active learning | S5-01 |
| S3-10 프로젝트 수명과 데이터 이동 | S1-08 |
| S4-01 DINOv3 Classification recipe | S1-05, S3-07 |
| S4-02 DINOv3 Segmentation recipe | S0-01, S1-05, S3-07 |
| S4-03 DINOv3 Patch Classification recipe | S1-05, S3-07 |
| S4-04 YOLO 일반 검출 recipe | S0-01, S1-05, S3-07 |
| S4-05 이상탐지 두 목적과 calibration | S0-02, S3-03, S3-07 |
| S4-06 OCR 검출과 인식 recipe | S1-05, S3-05, S3-07 |
| S4-07 회전 검출 production adapter | S1-05, S3-05, S3-07, S6-01 |
| S4-08 회전과 정렬 recipe | S1-05, S3-05 |
| S4-09 이미지 개선 실제 pair recipe | S1-05, S3-08 |
| S4-10 GAN 생성과 사람 검토 | S1-05, S3-04, S3-07 |
| S4-11 모든 작업의 실행 위치 통합 | S1-02, S1-05 |
| S4-12 AutoDL과 checkpoint 재개 | S1-02, S1-03 |
| S4-13 동일 cohort 모델과 전체 flow 평가 | S0-03, S2-06, S3-03, S3-07 |
| S4-14 모델 및 전체 flow 승인 | S0-04, S2-05 |
| S5-01 독립 검사 서비스 | S1-02, S1-09, S4-14 |
| S5-03 카메라와 video adapter | S1-05 |
| S5-05 제품 recipe와 traceability | S3-03, S2-10 |
| S5-06 장비 fleet와 단계 배포 | S0-04, S1-05, S4-14 |
| S5-07 관측과 운영 알림 | S1-02 |
| S5-08 data drift와 개선 반복 | S3-09, S4-13 |
| S5-09 백업과 복구 및 보존기한 | S1-08, S3-10 |
| S5-10 서비스 보안과 운영 설정 | S1-07 |
| S6-02 Windows CPU 설치 프로그램 | S1-09, S2-01 |
| S6-03 선택형 GPU와 runtime pack | S1-05 |
| S6-04 오프라인 설치와 업데이트 | S1-08, S5-09, S5-07 |
| S6-05 공개 CI와 source 재현성 | S0-08, S0-09 |
| S6-08 SDK와 자동화 API | S1-10, S4-14, S5-02 |
| S6-10 브라우저 및 확장 개발 경계 | S1-10, S1-05 |
| S7-01 기존 기능 전체 coverage 계약 | S6-05, S0-08 |
| S7-02 10모델군 실제 작업 시나리오 | S3-05, S4-01, S4-02, S4-03, S4-04, S4-05, S4-06, S4-07, S4-08, S4-09, S4-10, S4-14 |
| S7-03 Windows 실제 설치와 사용 QA | S2-08, S6-02, S6-04, S6-08 |
| S7-04 팀 동시 작업과 fault injection | S1-08, S5-06, S5-10, S6-04 |
| S7-05 데이터 규모와 연속 운전 | S3-01, S5-02, S5-07 |
| S7-06 공정 품질 승인과 장비 검증 | S4-13, S5-03, S5-04 |
| S7-07 공개 후보와 릴리스 판정 | S6-06, S6-09, S6-11, S0-08 |
| S7-08 파일럿 feedback과 지속 유지 | S6-07 |

S2-10 운영자 화면과 S3-09 현장 intake는 S5-01 runtime 이후 연결되는 후반 작업이다. S2/S3의 모든 task가 S5보다 앞에 끝나는 일정은 아니다. S6-01은 초기 inventory 작업이며, 공개 배포 결정 S6-11은 S6-06/S7-07의 공개 gate다. 내부 개발·CI discovery는 license 결정 대기를 이유로 중단하지 않는다.

## 신규 파일 생성 소유권

| 계획상 신규 경로 | 생성 owner |
| --- | --- |
| `backend/tests/test_service_integrity.py` | S0-08 |
| `scripts/e2e/service-integrity.spec.ts` | S0-08 |
| `docs/implementation-ledger/SERVICE-UPGRADE.md` | S0-08 |
| `backend/contracts/context.py` | S1-01 |
| `backend/engine/job_store.py` | S1-02 |
| `backend/engine/job_state.py` | S1-02 |
| `backend/contracts/capabilities.py` | S1-05 |
| `backend/engine/artifact_store.py` | S1-06 |
| `backend/remote/transfer.py` | S1-06 |
| `backend/storage/migrations.py` | S1-08 |
| `backend/frozen_entry.py` | S1-09 |
| `src/renderer/services/hostAdapter.ts` | S1-10 |
| `backend/api/routes_job_events.py` | S1-10 |
| `src/renderer/components/onboarding/FirstRunStudio.tsx` | S2-01 |
| `examples/demo-project` | S2-01 |
| `src/renderer/styles/design-tokens.css` | S2-02 |
| `src/renderer/components/layout` | S2-02 |
| `src/renderer/components/layout/WizardFooter.tsx` | S2-03 |
| `src/renderer/components/flowchart/FlowEditorWorkspace.tsx` | S2-04 |
| `src/renderer/components/common/EvidenceImageViewer.tsx` | S2-06 |
| `src/renderer/components/operator/OperatorWorkspace.tsx` | S2-10 |
| `backend/engine/dataset_index.py` | S3-01 |
| `backend/engine/input_adapters.py` | S5-02 |
| `backend/engine/capture_adapters.py` | S5-03 |
| `backend/engine/plc_adapters.py` | S5-04 |
| `backend/engine/mes_adapters.py` | S5-04 |
| `backend/engine/observability.py` | S5-07 |
| `src/renderer/components/operations` | S5-07 |
| `backend/storage/backup.py` | S5-09 |
| `THIRD_PARTY_NOTICES.md` | S6-01 |
| `docs/model-license-matrix.md` | S6-01 |
| `scripts/license_inventory.py` | S6-01 |
| `scripts/runtime_pack.py` | S6-03 |
| `backend/engine/runtime_update.py` | S6-04 |
| `.github/workflows/ci.yml` | S6-05 |
| `.github/workflows/windows-native.yml` | S6-05 |
| `.github/workflows/release.yml` | S6-05 |
| `scripts/check_service_plan.py` | S6-05 |
| `CONTRIBUTING.md` | S6-07 |
| `SECURITY.md` | S6-07 |
| `CODE_OF_CONDUCT.md` | S6-07 |
| `.github/ISSUE_TEMPLATE` | S6-07 |
| `.github/PULL_REQUEST_TEMPLATE.md` | S6-07 |
| `docs/api-integration.md` | S6-08 |
| `docs/quickstart-windows.md` | S6-09 |
| `docs/troubleshooting.md` | S6-09 |
| `scripts/publication_audit.py` | S6-09 |
| `docs/extension-contracts.md` | S6-10 |
| `scripts/e2e/model-lifecycle.spec.ts` | S7-02 |
| `scripts/e2e/windows-studio.spec.ts` | S7-03 |
| `backend/tests/test_service_failure_matrix.py` | S7-04 |
| `scripts/e2e/team-recovery.spec.ts` | S7-04 |
| `scripts/acceptance/scale_and_soak.py` | S7-05 |
| `docs/implementation-ledger/QUALITY-ACCEPTANCE.md` | S7-06 |
| `docs/implementation-ledger/FIELD-ACCEPTANCE.md` | S7-06 |
| `docs/release-checklist.md` | S7-07 |
| `docs/pilot-playbook.md` | S7-08 |
| `docs/maintainer-release-policy.md` | S7-08 |
| `playwright.config.ts` | S0-09 |
| `scripts/e2e/fixtures` | S0-09 |
| `scripts/tests/e2e-harness.test.cjs` | S0-09 |
| `docs/distribution-license-decision.md` | S6-11 |

동일 existing 파일은 여러 task가 순차 확장할 수 있다. 공유 API/router/schema/store/package lock은 통합 owner가 patch 순서를 조율한다. 위 신규 파일은 한 owner만 만들고 뒤 task는 handoff 이후 수정한다. S0-02 score UI→S0-05 상태 동작→S2-04 작업 화면 순서로 FlowchartStudio를 수정한다.

## 라이선스 inventory와 공개 결정의 분리

- S6-01: dependency/weights/source/conditions inventory와 unresolved 목록. LICENSE를 바꾸는 것은 완료 조건이 아니다.
- S6-11: 담당자의 공개 배포 정책 결정과 필요한 적법한 변경·고지. 공식 배포 S6-06/S7-07의 선행 gate다.
- 이번 계획 작성에서 LICENSE를 실제로 수정하지 않는다. 서명과 법적 조건이 미확정인 내부 artifact는 개발/검토용으로 표시한다.

## 기능 확장 acceptance

E01–E08은 spec의 2026-10-02 확장 계약과 `docs/service-upgrade-program.json`의 `scope_refinements`를 따른다. 각 확장의 owner_task가 핵심 계약을 구현하고 integration_tasks는 연결·UI·패키지·실행 근거를 확인한다. 신규 기능의 미구현 상태를 기존 task 완료로 대신하지 않는다.
