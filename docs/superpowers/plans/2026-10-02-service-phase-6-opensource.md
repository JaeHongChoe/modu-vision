# Windows 오픈소스 배포 실행 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. 이 문서는 검토용이며 새로운 구현 완료 상태가 아니다.

**Goal:** 외부 사용자가 개발 환경 또는 설치 프로그램으로 재현할 수 있게 한다.

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

## S6-01 오픈소스와 모델 라이선스 정책

**Files:**

- Create: `THIRD_PARTY_NOTICES.md`
- Create: `docs/model-license-matrix.md`
- Create: `scripts/license_inventory.py`
- Test: `backend/tests/test_service_s6_01.py`

**Interfaces:** `LicenseInventory(components, weights, conditions, unresolved_items) -> InventoryReceipt; public decision is S6-11`

**Consumes:** 현재 baseline source와 위 설계의 공통 계약

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 현재 MIT와 실제 포함 dependency/YOLO AGPL/DINOv3 별도 조건을 artifact별로 조사하여 source/license/notice/redistribution 검토 입력을 만든다. LICENSE 변경은 이 task의 완료 조건이 아니고 공개 배포 결정은 S6-11이다. 모듈 분리만으로 의무 면제를 주장하지 않는다.
- 자사 코드·의존성·가중치·학습 모델·sample data를 구분해 배포 여부와 필요한 고지를 기록한다. license 미확정 pack은 배포하지 않고 사용자 적법한 경로의 가중치 import를 제공한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s6_01.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s6_01.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S6-02 Windows CPU 설치 프로그램

**Files:**

- Modify: `build/electron-builder.yml`
- Modify: `build/installer.nsh`
- Modify: `scripts/build_backend_binary.py`
- Modify: `scripts/release_backend_acceptance.py`
- Test: `backend/tests/test_service_s6_02.py`

**Interfaces:** `WindowsCPURelease(installer, frozen_backend, inventory, source_sha)`

**Consumes:** S1-09, S2-01, S6-01

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- Python/Node 미설치 Windows 11 x64에서 non-admin 사용자 설치·첫실행·실제 CPU 추론·재실행·제거를 검증한다.
- 한글/공백 경로와 locked file를 처리하고 사용자 프로젝트는 제거 시 보존한다. Windows10 지원은 EOL/보안정책 및 별도 검증 후 선택 지원으로 표시한다.
- non-admin Studio와 elevated SCM registration은 서로 다른 설치 옵션이다. 서비스등록 refusal·rollback·uninstall은 소유한 서비스만 처리하며 개인 데이터와 Studio를 보존한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s6_02.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s6_02.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S6-03 선택형 GPU와 runtime pack

**Files:**

- Modify: `build/remote/requirements-models.lock`
- Create: `scripts/runtime_pack.py`
- Modify after S1-05 handoff: `backend/contracts/capabilities.py`
- Test: `backend/tests/test_service_s6_03.py`

**Interfaces:** `RuntimePack(os, arch, python_abi, accelerator, deps, weights, licenses, sha256)`

**Consumes:** S1-05, S6-01, S6-02

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- CPU 기본 설치와 NVIDIA/optionalOCR/DICOM/OpenVINO pack의 필요용량·호환driver·출처·checksum을 구분한다.
- Linux worker와 Windows localCUDA 조합을 각각 preflight·native 실행으로 검증한다. unavailable optional runtime은 기능을 조용히 가짜 실행하지 않는다.
- package library에서 선택 package의 ONNX/OpenVINO·정량화/embedded optimization을 job으로 실행하고 actual supported hardware·measured precision/cohort drift·변환/추론 receipt를 기록한다. CPU/GPU/iGPU/NPU/Jetson/MIG 조합은 실제 증거별 지원표이며 즉시 모든 장치를 지원한다고 선언하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s6_03.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s6_03.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S6-04 오프라인 설치와 업데이트

**Files:**

- Create: `backend/engine/runtime_update.py`
- Modify: `build/release-hooks.cjs`
- Modify: `scripts/release-readiness.cjs`
- Modify after S5-07 handoff: `src/renderer/components/operations`
- Test: `backend/tests/test_service_s6_04.py`

**Interfaces:** `UpdatePlan(app, runtime, protocol, schema, packs) / recover_update(intent)`

**Consumes:** S1-08, S5-09, S6-02, S6-03, S5-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 인터넷 차단 환경에서 checksum/서명 확인→설치→known-image 실행을 검증한다. app/API/worker/runtime/schema compatibility matrix를 적용한다.
- 다운로드/설치/DBmigration 각 지점 중단 후 이전 설치와 데이터로 복구한다. 실행 중 학습·검사와 충돌하는 업데이트는 안전한 실행 창을 안내한다.
- update의 pinned trust authority/publisher/key·manifest signature·pack inventory·key rotation/revocation·offline trust provision·downgrade policy를 고정한다. 변조 manifest/다른 publisher/누락추가 pack/폐기 key/의도하지 않은 구버전을 거절한다. 승인된 복구 rollback은 별도 기록한다. cutover 후 새 쓰기가 있으면 검증된 inverse 또는 forward recovery만 수행한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s6_04.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s6_04.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S6-05 공개 CI와 source 재현성

**Files:**

- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/windows-native.yml`
- Create: `.github/workflows/release.yml`
- Create: `scripts/check_service_plan.py`
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `requirements.txt`
- Modify: `build/remote/requirements-models.lock`
- Test: `backend/tests/test_service_s6_05.py`

**Interfaces:** `BuildManifest(source_sha, lockfiles, toolchain, platform, sbom, checksums)`

**Consumes:** S0-08, S6-01, S6-02, S0-09

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- PR에서 PythonCPU/renderer/type/build/contract 회귀를 실행하고 Windows native smoke를 별도로 운영한다. source/lock/runtime inventory를 release artifact에 묶는다.
- 외부 PR에는 secret/GPU 접속을 제공하지 않는다. remoteGPU는 승인된 내부 runner, signing은 protected release workflow에서만 수행한다. bit-for-bit 재현은 측정 전 주장하지 않는다.
- Windows native와 Linux CPU/native worker CI를 명시적으로 포함한다. Electron/Python/Node/dependency 지원 및 보안 버전 검토와 lock upgrade를 초반 작업에 포함하고 특정 최신 버전은 official release 확인 후 결정한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s6_05.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s6_05.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S6-06 서명과 릴리스 채널

**Files:**

- Modify: `build/release-hooks.cjs`
- Modify: `scripts/release-readiness.cjs`
- Modify: `docs/release-platform-matrix.md`
- Test: `backend/tests/test_service_s6_06.py`

**Interfaces:** `ReleaseChannel(version, checksums, signatures, minimum_protocol, source_link)`

**Consumes:** S6-02, S6-04, S6-05, S6-11

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- Windows Authenticode·checksum·release notes·지원표·source link를 동일 artifact에 연결한다. stable/beta와 unsigned development artifact를 구분한다.
- 서명 후 byte 변화는 기존 receipt를 무효로 하며 실제 installer/실행 inventory를 다시 검증한다. signing identity가 없으면 unsigned 상태를 공개하고 signed gate는 pending이다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s6_06.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s6_06.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S6-07 외부 기여와 프로젝트 운영

**Files:**

- Create: `CONTRIBUTING.md`
- Create: `SECURITY.md`
- Create: `CODE_OF_CONDUCT.md`
- Create: `.github/ISSUE_TEMPLATE`
- Create: `.github/PULL_REQUEST_TEMPLATE.md`
- Test: `backend/tests/test_service_s6_07.py`

**Interfaces:** `ContributionPolicy / SecurityDisclosure / MaintainerReview`

**Consumes:** S6-05

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 새 PC에서 clone→dependency setup→CPUdemo→focusedtest→PR 절차를 재현한다. architecture/module map·coding rule·issue/feature/bug templates와 ownership을 제공한다.
- 지원OS/model/runtime matrix·change policy·deprecation·release governance를 명시한다. 보안 report에 실제 이미지·secret을 공개 첨부하지 않도록 안내한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s6_07.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s6_07.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S6-08 SDK와 자동화 API

**Files:**

- Extend directory: `native_runtime`
- Extend directory: `examples`
- Extend directory: `backend/api`
- Create: `docs/api-integration.md`
- Test: `backend/tests/test_service_s6_08.py`

**Interfaces:** `Versioned API / CLI / Python SDK / C++ and C# bridge`

**Consumes:** S1-10, S4-14, S5-02

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- Python/C++/C#에서 동일 flow package·결과schema·timeout/cancel/error를 사용하고 설치/런타임 의존성을 표시한다.
- 실제 Windows 빌드/run 예제와 Linux 예제, REST/OpenAPI·batchCLI·headless 실행을 검증한다. wrapper가 native-only inference인 것처럼 표현하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s6_08.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s6_08.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S6-09 공개 위생과 지원 자료

**Files:**

- Modify: `README.md`
- Create: `docs/quickstart-windows.md`
- Create: `docs/troubleshooting.md`
- Create: `scripts/publication_audit.py`
- Test: `backend/tests/test_service_s6_09.py`

**Interfaces:** `PublicationReport(paths, findings, whitelist, release_artifacts)`

**Consumes:** S6-01, S6-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 소스/commit text/docs/example/log/package에서 credentials·실데이터·개인경로·사설서버·불필요한 비교/회사명을 검사한다. required third-party 저작권·license 고지는 보존한다.
- redacted diagnostics와 설치/학습/플로우/서비스 오류 해결 자료·지원 요청 bundle를 제공한다. history rewrite는 별도 동작이며 이번 계획 단계에서 수행하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s6_09.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s6_09.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S6-10 브라우저 및 확장 개발 경계

**Files:**

- Modify after S1-10 handoff: `src/renderer/services/hostAdapter.ts`
- Create: `docs/extension-contracts.md`
- Modify after S1-05 handoff: `backend/contracts/capabilities.py`
- Test: `backend/tests/test_service_s6_10.py`

**Interfaces:** `ModelAdapter / InputAdapter / DeliveryAdapter / StorageAdapter / BrowserHost`

**Consumes:** S1-10, S6-07, S1-05

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 동일 renderer의 browser build와 팀 서버 연결·upload/download·권한·flow viewer를 검증한다. desktop native 기능 차이를 명확히 보여준다.
- 외부 기여자가 새 모델/장비 adapter를 contract fixture로 구현한다. plugin은 version·권한·라이선스 검증을 거치며 arbitrary 코드 실행이 trusted inference와 섞이지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s6_10.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s6_10.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S6-11 공개 배포 라이선스 결정

**Files:**

- Create: `docs/distribution-license-decision.md`
- Modify: `LICENSE`
- Modify after S6-01 handoff: `THIRD_PARTY_NOTICES.md`
- Modify after S6-01 handoff: `docs/model-license-matrix.md`
- Test: `backend/tests/test_service_s6_11.py`

**Interfaces:** `DistributionDecision(artifact_scope, license, notices, authority, approval_status)`

**Consumes:** S6-01

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- S6-01 inventory를 근거로 자사 코드·dependency·weights·trained package별 공개 배포 정책을 검토하고 담당자가 결정한다. 이번 계획 작성에서는 LICENSE를 바꾸지 않는다.
- MIT 유지/적법한 재라이선스/적법한 dependency 선택 등은 검토 결과와 권한에 따라 실행한다. unresolved distribution은 공개 gate를 보류하며 내부 테스트·CI·source 조사 전체를 막지 않는다. 공개 artifact와 required notice를 일치시킨다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s6_11.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s6_11.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## phase 완료 판정

- 해당 task의 계약·영속·재열기·실패·handoff 근거가 존재한다.
- 새로운 interface가 다음 phase 및 legacy ID와 연결되어 있다.
- 테스트 skip/부족 데이터/미지원 target/서명/실장비 미검증은 이유와 owner를 남긴다.
- 조건이 없는 항목의 `not_required`는 사유와 reviewer가 있어야 하며 pending을 임의 완료로 바꾸지 않는다.

## 추가 기능 acceptance 연결

구현 시 spec 확장 계약과 registry의 acceptance를 함께 사용한다. 다른 phase의 owner가 만든 계약은 검토한 뒤 소비한다.

- E07 배포 준비 화면의 전체 의존성 점검표: owner S5-05; 이 phase 연결 S6-03, S6-10
