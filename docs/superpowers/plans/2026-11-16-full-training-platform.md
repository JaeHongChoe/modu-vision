# Full Training Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Geti의 전체 application과 학습 library를 모두비전 안에 통합해 데이터 준비부터 모델 학습·평가·사용까지 연결한다.

**Architecture:** `app/v3.3.0` 전체 소스를 고정 의존성으로 유지하고, 독립 runtime/build와 모두비전의 authenticated gateway·binding ledger를 연결한다. 기존 프로젝트/10개 모델군/네이티브 기능은 확장 경로로 보존하며 source import와 runtime/제품 통합의 완료를 구분한다.

**Tech Stack:** 고정 Geti SHA `ddcf394d550bf1b84ca262888260043af361f5e7`, 별도 Python 3.14/Geti React 19.1.1 환경; 기존 Python 3.13/FastAPI/Electron/React 18.3.1; Node 24, Geti UI npm >=11.14.0.

**Spec:** [2026-11-16-full-training-platform-design.md](../specs/2026-11-16-full-training-platform-design.md)

## Global Constraints

- 작업 브랜치는 `feat/full-training-platform-20261116`, 시작 head는 PR34 `a92aea616d74d5b5195c876d3b01b5366242a224`이다. PR34의 진행 중 CI를 취소·재실행하지 않는다.
- 업스트림은 `app/v3.3.0`/`ddcf394d550bf1b84ca262888260043af361f5e7`에 고정한다. 소스 확보와 runtime·앱 통합 완료를 구분한다.
- 현재 React 18.3.1/Python 3.13 환경을 Geti React 19.1.1/Python >=3.14,<3.15로 덮어쓰지 않는다.
- 최초 실행은 임시 data/log/cache root와 loopback 별도 포트만 사용한다. 기존 앱·8443·사용자 원본/정답/모델/프로젝트·GPU·다른 자동화를 바꾸지 않는다.
- telemetry/analytics·자동 weight 다운로드·선택적 Ultralytics extra를 초기 기본 profile에서 제외한다. 실제 GPT·유료 호출·사용자 이미지 외부 전송·자동 로그인·기존 자격증명 추출은 수행하지 않는다. 가능성 시험은 로그인과 분리하고, 제품 인증 검증은 소유 QA 계정/세션 fixture로 한다.
- 기존 fingerprint/labelset/분할·heldout/기계 초안/작업 소유/취소·실제 종료/품질·서비스 ACK 계약을 유지한다.
- `classification`, `detection`, `instance_segmentation`의 변환만 명시적으로 지원한다. semantic segmentation 및 나머지 모델군을 암묵적으로 같은 task로 바꾸지 않는다.
- 사용자 source migration은 명시적 복사/변환과 readback 후 선택 사항이며 기존 프로젝트는 그대로 재개할 수 있어야 한다.

## Review Focus

- 응답이 사라진 학습 시작: 기존 Geti/모두비전 job 연결을 재조회하고 학습을 중복 제출하지 않는다 — Task 3.
- 계정 전환·다른 프로젝트 UUID·로그아웃: gateway가 정확 범위를 검증하고 retained 요청/이미지 ticket을 거절한다 — Task 3.
- 이름은 같지만 라벨셋/원본/분할이 달라진 자료: 학습·평가·모델을 이전 근거에 붙이지 않는다 — Task 4.
- instance mask와 semantic mask, specialist checkpoint 형식: 손실 없는 변환 또는 명시적 미지원이며 기존 동작을 보존한다 — Task 4.
- 플랫폼 child 오류·취소·재시작·disk 부족: 종료/자원/완료 artifact를 분리해 표시하고 기존 앱/자료를 손상하지 않는다 — Tasks 2, 3, 6.

---

## Planned file ownership

아래 새 product 경로는 구현 예정이며 현재 존재/연결됐다는 표시가 아니다. upstream 수정이 필요하면 고정 patch manifest로 관리한다.

| 경로 | 책임 |
| --- | --- |
| `platforms/geti/upstream` | 전체 pinned source gitlink; application와 library 보존 |
| `platforms/geti/upstream.lock.json`, `README.md`, `UPSTREAM-NOTICES.md` | source identity·license·실행 요구와 현재 qualification |
| `scripts/geti-platform.cjs` | 독립 developer 가능성 실행과 owned child cleanup |
| `scripts/tests/geti-platform.test.cjs` | pin/environment/port/path/child lifetime 계약 |
| `src/main/platform/getiSupervisor.ts` | 설치된 child 소유·진단·종료; 기존 supervisor와 협력 |
| `backend/platforms/geti/{bindings,client,policy}.py` | durable project/dataset/job/model 매핑, allowlisted HTTP client, scope 검증 |
| `backend/api/routes_platform_geti.py` | 인증된 명시 API; 무제한 proxy 금지 |
| `backend/tests/test_geti_platform_*.py` | 권한/중복/손실/format·기존 계약 회귀 |
| `src/renderer/components/platform/*`, `src/renderer/services/getiPlatform.ts` | 현재 shell의 프로젝트/작업 UI 연결; child secret 미노출 |
| `scripts/e2e/geti-platform-*.spec.ts` | 소유 synthetic 실제 runtime·재접속·실패 흐름 |
| `docs/geti-platform.md`, `docs/verification/2026-11-16-full-training-platform.md` | 사용자 실행/지원 범위와 단계별 증거 |

### Task 1: 전체 source 기반과 재현 가능한 독립 환경 (M0)

**Files:** `platforms/geti/*`, `scripts/geti-platform.cjs`, `scripts/tests/geti-platform.test.cjs`.

**Interfaces:** Produces `readUpstreamLock(root): {repository,tag,commit,applicationPath,libraryPath}`와 `verifyUpstream(root): {valid:boolean,errors:string[]}`; 성공은 source pin 확인만 의미한다.

- [x] pin 불일치, dirty upstream, 빠진 application/library/license, manifest 누락을 거절하는 Node test를 작성하고 실패를 확인한다.
- [x] 전체 source를 gitlink로 가져오고 lock/notice를 작성한다. 고정 source와 package metadata에서 toolchain 요구를 읽어 `verifyUpstream`을 구현한다. global 환경을 변경하지 않는다.
- [x] `node --test scripts/tests/geti-platform.test.cjs`로 성공/거절 양쪽을 확인한다. `git diff --cached --submodule=short`로 정확 SHA와 전체 source 경계를 읽는다. 실제 source check는 2,987 tracked objects, backend589/UI1,239/library987을 확인했고 runtime/product integration은 false다.
- [ ] README에 M0의 완료와 M1 이후 미완료를 구분하고 단독 source/contract commit을 만든다.

### Task 2: 실제 macOS ARM CPU 전체 runtime 가능성 시험 (M1)

**Files:** `scripts/geti-platform.cjs`, 같은 test 파일, `scripts/e2e/geti-platform-lifecycle.spec.ts`, 검증 문서.

**Interfaces:** Consumes Task 1 source identity. Produces `launchIsolatedGeti({root,python,uiDir,dataDir,port}): OwnedGetiHandle`, `OwnedGetiHandle.stop(): Promise<{exitCode,portsClosed}>`; runtime receipt는 source/toolchain/data root·PID creation identity·UI/backend hash를 포함한다.

- [ ] 잘못된 Python/기존 data root/비loopback bind/점유 port를 실행 전에 거절하고 자신이 시작한 child만 종료하는 test를 작성·실패 확인한다.
- [ ] 별도 Python 3.14/React 19 build 환경에서 고정 app/library 의존성을 해결한다. optional Ultralytics와 자동 pretrained 다운로드를 제외한 실제 dependency manifest를 저장한다. Mac wheel/ABI 실패는 원본 로그를 남기고 검증하지 않은 package substitution으로 숨기지 않는다.
- [ ] telemetry·외부 송신·weight 다운로드 경로를 고정 source에서 확인하고 명시적으로 차단한다. local synthetic fixture와 이미 검증한 로컬 weight만 사용한다.
- [ ] 실제 UI/backend를 별도 port에 띄워 synthetic dataset 생성/업로드/라벨 readback→실제 CPU 학습→평가→모델 variant export→재접속을 한 번 실행한다. 실제 모델이 없는 경우 학습 성공을 조작하지 않고 해당 milestone을 미완료로 남긴다.
- [ ] 실행한 job/metric/cohort/artifact SHA와 worker 종료·port closure를 기록한다. health/UI만 성공한 경우와 전주기 성공을 구분한다. test·receipt·지원 범위를 commit한다.

### Task 3: 인증된 gateway·작업 binding·설치 supervisor (M2)

**Files:** `backend/platforms/geti/{bindings,client,policy}.py`, `backend/api/routes_platform_geti.py`, `backend/main.py`, `src/main/platform/getiSupervisor.ts`, 기존 `src/main/supervisor.ts`, `backend/tests/test_geti_platform_authority.py`, `test_geti_platform_jobs.py`.

**Interfaces:** `PlatformProjectBinding(workspace_id,project_id,instance_id,geti_project_id)`와 `PlatformJobLink(modu_job_id,geti_job_id,idempotency_key,spec_sha256,origin_context)`를 durable 저장한다. `GetiClient.submit_training(binding,request,idempotency_key)`, `read_job(link)`, `request_cancel(link)`는 scoped binding을 받으며 arbitrary path/host를 받지 않는다.

- [ ] 사용자 A의 session/프로젝트로 B의 UUID 요청, logout·scope generation 변경 후 retained 요청, grant 철회, 직접 child port 접근을 거절하는 test를 먼저 작성·실패 확인한다.
- [ ] child는 loopback에서 supervisor가 소유하고 gateway가 프로젝트·역할·허용 endpoint를 검증한다. `/platform/geti/` UI와 `/platform/geti/api/` API mount, asset/router/API/TUS·Location·media URL/SSE/WebRTC rewrite를 versioned patch에서 함께 구현한다. child middleware는 health 포함 instance secret을 검증하고 gateway만 header를 주입한다. secret은 browser bundle/URL에 보내지 않는다. 직접 child 접근·다른 project media·logout 중 SSE/업로드를 거절하는 test를 통과한다.
- [ ] local submission reservation과 별도로 upstream `POST /api/jobs`/`app/core/jobs/control_plane/queue.py`에 durable request correlation patch를 구현한다. request ID+spec digest unique binding을 job publication 전에 저장하고 재시작 후에도 같은 ID로 원래 job을 찾게 한다. 응답 유실·동시 동일 key·다른 body/same key·child restart와 memory job history 소멸 test를 통과한다. patch/결과 식별이 불완전하면 `submission_unconfirmed`를 남기고 자동 재제출하지 않는다.
- [ ] queued/running/cancel requested/worker exited/artifact completed를 각각 기존 job ledger와 연결한다. cancel 응답만으로 resource release를 보고하지 않는 test를 확인한다.
- [ ] focused backend·main tests와 타입 검사를 통과하고, 두 소유 QA 계정/세션 fixture·synthetic project의 로그인·로그아웃·재접속 경로로 교차 범위 차단을 확인한 뒤 commit한다. 기존 사용자 자격증명을 읽거나 자동 로그인하지 않는다.

### Task 4: 기존 데이터·10모델군·format별 모델 사용 연결 (M3)

**Files:** `backend/platforms/geti/bindings.py`, 새 `dataset_transfer.py`, `model_runtime.py`, `backend/tests/test_geti_platform_dataset_transfer.py`, `test_geti_platform_model_runtime.py`; 기존 `training_provenance.py`, `model_catalog.py`, `model_execution.py`, `routes_flowchart.py`의 작은 adapter 호출부.

**Interfaces:** `prepare_dataset_copy(project,version_id,target_binding)->TransferReceipt`와 `verify_dataset_copy(receipt)->VerificationResult`; receipt에는 version/manifest/labelset/split SHA, original-to-media mapping과 counts가 있다. `resolve_platform_model(project,model_ref)->VerifiedPlatformModel`은 format/variant/SHA를 검증한다.

- [ ] classification tag/detection bbox/instance mask의 synthetic 왕복으로 image count·class ID/name·coordinates·mask·split·source hash 일치를 먼저 test한다. 다른 labelset, 바뀐 source·split, unsupported semantic mask·specialist truth는 오류로 남기고 원본 bytes가 그대로인지 단언한다.
- [ ] 원본 ID→Geti media UUID의 실제 train/val/test 구성원 목록과 공통 원본 그룹을 별도로 동결한다. upstream dataset revision/subset readback과 정확히 대조하고 자동 재분할/heldout 섞임을 거절하는 test를 통과한 뒤 명시적 copy/import binding을 publish한다. 실패·disk 부족·동시 label 변경 때 이전 프로젝트를 보존한다.
- [ ] Geti label edit를 모두비전에 채택할 때 기존 revision/source/split compare-and-swap을 검사해 새 라벨 revision·새 immutable version을 만든다. 양쪽 동시 편집은409 conflict로 보존하며 이전 version/source bytes를 바꾸지 않는 왕복 test를 통과한다.
- [ ] Geti variant 형식별 실제 reconstruction/inference adapter와 모델 목록을 연결한다. `.pt`/OpenVINO/ONNX가 같은 loader로 처리되지 않는지 test하고, local synthetic 사진의 실제 prediction·원본 좌표를 대조한다.
- [ ] 기존 10개 모델군 capability·원격/로컬 실행과 이전 저장 프로젝트 reopen 회귀를 통과한다. validation과 독립 test SHA·threshold·평가 history·품질/서비스 ACK를 유지하는 contract test를 추가한다.
- [ ] focused tests와 실제 변환/추론 증거를 저장하고 adapter commit을 만든다. 지원하지 않은 task를 UI에서 명확히 표시한다.

### Task 5: 모두비전의 한 작업 UI와 한국어 흐름 (M4)

**Files:** `src/renderer/components/platform/*`, `src/renderer/services/getiPlatform.ts`, Geti용 versioned UI patch/translation manifest, `DESIGN.md`, `UX-CONTRACT.md`, 관련 styles, `scripts/e2e/geti-platform-journey.spec.ts`.

**Interfaces:** 현재 account/project context를 받아 `PlatformWorkspace`가 데이터→준비→학습→결과→검사 경로를 렌더한다. React 19 upstream UI는 별도 build boundary이며 current React 18 component import와 전역 state를 공유하지 않는다.

- [ ] task별 시작/재접속/empty/loading/error/unsupported 상태에서 한 주요 행동과 실제 다음 화면을 확인하는 failing UI tests를 작성한다. 계정·프로젝트 전환은 state/asset 요청을 무효화해야 한다.
- [ ] Geti의 실제 작업대와 연결된 모델/데이터 UI를 모두비전 shell에 통합하고 한국어 용어·읽기 쉬운 이름·같은 색/키보드/복구 패턴을 적용한다. 사용자가 두 프로젝트 목록을 수동 동기화하는 동작을 없앤다.
- [ ] 기존 학습 전 처리 블록·FFT recipe·specialist/remote 도구·GPT context/typed command를 확장 경로로 연결한다. 미지원 모델/형식은 숨겨진 기능 소실로 처리하지 않는다.
- [ ] 실제 synthetic 프로젝트로 upload→라벨 저장→준비→train→비교→오류 사진 수정→모델 사용→reload 왕복을 검증한다. 720px·assistant 열린 폭·양 theme·한글 긴 이름·키보드와 503/child crash 복구를 확인한다.
- [ ] browser/Electron 결과·시각 캡처를 확인하고 frontend/backend 독립 검토 후 commit한다. synthetic runtime 결과와 실제 현장 품질·첫 사용자 완주는 분리한다.

### Task 6: 정확 head CI·패키징·기존 데이터·매뉴얼 (M5)

**Files:** 실제 bundle manifest/hooks, `.github/workflows`의 명시 qualification lane, `docs/geti-platform.md`, 매뉴얼 해당 절, 검증 문서와 release receipt.

**Interfaces:** release receipt에 parent/source/upstream SHA, UI/backend/toolchain/optional model manifests, installed runtime hash, legacy readback, rollback data compatibility를 기록한다.

- [ ] 의존성/notice drift·dirty upstream·누락 Python3.14 runtime·cross-target binary·artifact SHA 불일치를 release 단계에서 거절하는 test를 작성·실패 확인한다.
- [ ] 표준 `npm run typecheck`, `npm run test:renderer`, `npm run build`, `npm run typecheck:e2e`, 관련 backend/runtime/lifecycle tests와 source/action evidence checks를 통과한다. harness 변경 시 `npm run test:e2e:harness`도 통과한다. 전체 제품 과거 실패를 이번 focused 통과로 소거하지 않는다.
- [ ] 정확 최종 head fast가 성공한 뒤 같은 head full을 한 번 시작하고 run ID를 기록한다. full 성공 후 증거 gate의 최초 누락-proof 실패만 허용 범위에서 재평가한다. CI 중복 기동·cancel·보호 우회를 하지 않는다.
- [ ] 실제 target OS/architecture에서 Geti runtime 포함 package를 만들고, 소유 기존 프로젝트의 이미지/라벨/모델/flow readback·새 프로젝트 전주기·앱/웹 재접속·정상종료·rollback을 검증한다. Windows/Linux/Mac 중 실제 미검증은 분리한다.
- [ ] 매뉴얼은 실행한 버전·지원 task·데이터 이동과 오류 복구·기존 확장 경로에 맞춰 그림/설명을 갱신한다. source/CI/설치/서비스 운영/현장 품질의 완료를 구분해 사용자에게 보고한다. 실행본 교체는 그때의 미저장/실행 중 상태와 기존 권한을 확인하고 수행한다.

## 진행 기록 규칙

각 milestone의 실제 결과·실패·미검증을 `.superpowers/sdd/2026-11-16-full-training-platform/`의 별도 receipt에 남긴다. 본 계획의 체크는 검증을 통과한 단계만 갱신한다. 초기 source import나 한 번의 UI boot를 전체 플랫폼 완성으로 표시하지 않는다.
