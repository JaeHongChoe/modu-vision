# Windows와 서버 공통 기반 실행 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. 이 문서는 검토용이며 새로운 구현 완료 상태가 아니다.

**Goal:** 로컬 및 팀 모드에서 같은 영속 작업·버전·권한 계약을 사용한다.

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

## S1-01 프로젝트와 실행 문맥 공통 계약

**Files:**

- Modify: `backend/main.py`
- Modify: `backend/api/shared_authorization.py`
- Create: `backend/contracts/context.py`
- Modify: `src/renderer/services/api.ts`
- Test: `backend/tests/test_service_s1_01.py`

**Interfaces:** `ProjectContext(workspace_id, project_id, actor_id, mode) / ArtifactRef(id, revision, sha256)`

**Consumes:** S0-08

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- local/team 모드에서 API와 이벤트가 명시적인 project 문맥을 가진다. 두 사용자 동시 요청의 프로젝트·이미지·작업이 섞이지 않는다.
- 기존 프로젝트 ID와 모델·flow history를 보존하며 서버 절대 경로 대신 ID 기반 참조를 사용한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s1_01.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s1_01.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S1-02 영속 작업 원장과 멱등 제출

**Files:**

- Create: `backend/engine/job_store.py`
- Create: `backend/engine/job_state.py`
- Modify: `backend/api/routes_training.py`
- Modify: `backend/engine/training_engine.py`
- Test: `backend/tests/test_service_s1_02.py`

**Interfaces:** `submit_job(spec, idempotency_key) -> JobRef / transition_job(id, expected_revision, event)`

**Consumes:** S1-01

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- API가 접수한 작업은 강제 종료·재시작 후 유실되지 않는다. 같은 idempotency key와 같은 spec은 같은 job, 다른 spec은 충돌을 반환한다.
- jobs/attempts/events/artifacts/cancel-intent/parent-child를 transaction으로 저장한다. 재시작 시 동일 GPU 작업을 두 번 시작하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s1_02.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s1_02.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S1-03 스케줄러와 작업 소유권

**Files:**

- Modify: `backend/engine/shared_scheduler.py`
- Modify: `backend/engine/scheduler.py`
- Modify after S1-02 handoff: `backend/engine/job_store.py`
- Test: `backend/tests/test_service_s1_03.py`

**Interfaces:** `claim_job(worker_id, capabilities) -> AttemptLease(fence, expires_at) / publish_result(attempt, manifest)`

**Consumes:** S1-02

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- queue·priority·project quota·GPU/MIG memory 예약·heartbeat를 작업 원장과 연결한다. lease fencing으로 이전 attempt의 결과 게시를 거절한다.
- 불확실 worker는 확인 전 예약을 유지한다. 우선순위와 공정성·작업별 예산·대기 이유를 표시하며 타 사용자의 프로세스를 종료하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s1_03.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s1_03.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S1-04 취소와 장애 상태 통합

**Files:**

- Modify: `backend/engine/local_training_worker.py`
- Modify: `backend/remote/coordinator.py`
- Modify after S1-02 handoff: `backend/engine/job_state.py`
- Modify: `src/renderer/components/training/TaskCenter.tsx`
- Test: `backend/tests/test_service_s1_04.py`

**Interfaces:** `request_cancel(job_id) -> CancelIntent / reconcile_attempt(...) -> ObservedState`

**Consumes:** S1-02, S1-03

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- cancel_requested→취소 ACK→owned process 종료→예약 해제의 증거가 분리된다. 접속 실패를 취소 완료로 바꾸지 않는다.
- 재연결은 같은 작업을 관찰한다. PID 재사용·네트워크 끊김·디스크 부족·OOM·앱 재시작에서 정확한 다음 행동을 제공한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s1_04.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s1_04.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S1-05 범용 worker 등록과 지원표

**Files:**

- Modify: `backend/remote/coordinator.py`
- Modify: `backend/remote/operations.py`
- Modify: `backend/api/routes_model_catalog.py`
- Modify: `backend/engine/model_catalog.py`
- Create: `backend/contracts/capabilities.py`
- Test: `backend/tests/test_service_s1_05.py`

**Interfaces:** `WorkerCapabilities(protocol_version, os, arch, devices, tasks, stages, runtime_digest, weight_inventory)`

**Consumes:** S1-01, S1-03

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 호스트 번호 없이 로컬/SSH/Docker worker를 등록하고 CPU/CUDA·모델군·train/eval/infer/search/export 지원 여부를 실제 preflight로 검증한다.
- Windows에서 MPS를 노출하지 않는다. 미지원·미설치·미검증·검증 완료를 구분하며 unsupported 조합과 조용한 로컬 fallback을 차단한다.
- 선택형 HTTPS pull worker를 같은 계약으로 추가할 수 있게 transport 경계를 유지한다. worker별 최소 권한 등록·자격증명 회전·취소 확인과 client/API/worker protocol 호환을 검사한다. pull 연결은 사설망에서도 운영하며 public exposure를 전제로 하지 않는다. worker/profile/GPU/runtime별 resource ACL과 secret 소유 범위를 검사한다. 다른 프로젝트가 ID를 알아도 probe·submit·cancel·credential read가 불가능하다. team enrollment에 필요한 TLS/auth/trust는 초기 gate다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s1_05.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s1_05.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S1-06 저장소와 artifact 게시

**Files:**

- Create: `backend/engine/artifact_store.py`
- Modify: `backend/engine/dataset_fingerprint.py`
- Modify: `backend/api/routes_dataset.py`
- Create: `backend/remote/transfer.py`
- Test: `backend/tests/test_service_s1_06.py`

**Interfaces:** `ArtifactStore.put_verified(stream, expected_hash) -> ArtifactRef / open(ref)`

**Consumes:** S1-01

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 원자적 게시·중단 업로드 재개·checksum·중복 cache·quota·쓰기 금지 원본·객체별 권한을 검증한다.
- 로컬 파일 저장소부터 시작해 서버 파일/NAS·S3 호환 adapter를 확장한다. 메타데이터 DB와 객체 게시 실패를 보상 처리한다.
- artifact는 staging→verified→referenced로 게시한다. DB commit 실패·같은 hash의 다른 프로젝트 참조·orphan GC 유예·backup pin을 검증한다. 실패 upload cleanup과 GC가 다른 정상 참조의 객체를 지우지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s1_06.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s1_06.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S1-07 계정과 프로젝트 권한

**Files:**

- Modify: `backend/engine/shared_accounts.py`
- Modify: `backend/api/routes_accounts.py`
- Modify: `backend/api/shared_authorization.py`
- Test: `backend/tests/test_service_s1_07.py`

**Interfaces:** `authorize(actor, action, project, resource_revision) -> PermissionDecision`

**Consumes:** S1-01

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- viewer/labeler/trainer/reviewer/owner 역할의 HTTP·WebSocket·이미지·artifact·job·fleet 교차 프로젝트 접근을 거절한다.
- 계정 비활성·세션 만료·역할 변경을 실행/승인/배포 시 다시 적용한다. 로컬 개인 모드는 외부 네트워크 공개와 구분한다.
- workspace/조직과 프로젝트 membership을 명시하고 선택형 OIDC/SSO adapter를 제공한다. local actor는 인증된 team reviewer와 구분하고 UI 요청의 reviewer 이름만으로 팀 승인을 만들지 않는다. browser에는 HttpOnly/Secure/SameSite session cookie와 CSRF 보호, origin/CORS allowlist를 사용한다. Desktop/SDK bearer는 제한된 안전 저장소와 HTTPS로 사용한다. URL/query에 token을 넣지 않으며 WS는 session 검증을 사용한다. 권한 철회 시 열린 WS와 upload/download 재개 요청도 즉시 차단한다.
- 기존 계정·password hash·session policy·프로젝트 membership와 역할을 migration에서 보존하고 계정 재등록 없이 새 API에서 검증한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s1_07.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s1_07.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S1-08 기존 JSON과 DB migration

**Files:**

- Modify: `backend/engine/project_migration.py`
- Create: `backend/storage/migrations.py`
- Modify: `backend/engine/project_archive.py`
- Modify: `backend/remote/profiles.py`
- Modify: `backend/remote/coordinator.py`
- Modify: `backend/engine/shared_scheduler.py`
- Modify: `backend/engine/shared_accounts.py`
- Test: `backend/tests/test_service_s1_08.py`

**Interfaces:** `migrate_project(source, dry_run=True) -> MigrationReport / restore_migration(backup)`

**Consumes:** S1-02, S1-06

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- migration dry-run→backup→count/hash/revision 비교→원자적 cutover→복구를 검증한다. 기존 원본·labels·split·모델·flow·결과·승인을 보존한다.
- SQLite를 NAS에서 여러 호스트가 직접 열지 않는다. 최초 team 모드는 단일 API/scheduler, PostgreSQL adapter는 다중 API 요구와 부하 검증 후 지원한다.
- migration 전에 writer 중지·job drain/저장·backup revision 고정·cutover journal을 수행한다. cutover 후 정상 쓰기가 발생하면 오래된 backup의 무조건 restore를 금지하고 검증된 역변환 또는 forward recovery를 선택한다. 기존 지원 backbone/decoder checkpoint의 offline 재구성·평가·flow/export compatibility를 fixture로 유지한다. 신규 adapter가 기존 config/좌표/score 의미를 바꾸면 명시적 version migration을 요구한다.
- 앱/서버 전역 local_jobs·remote_jobs·resource leases·compute profiles·accounts/memberships도 dry-run migration한다. 실제 살아 있는 owned worker와 불확실 lease는 receipt/identity/fence로 adopt하고 중복 실행/고아 예약을 만들지 않는다. 복사본에서 타 설치의 token/lease를 새 실행 권한으로 활성화하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s1_08.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s1_08.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S1-09 Windows 프로세스와 실행 entrypoint

**Files:**

- Modify: `src/main/supervisor.ts`
- Modify: `backend/engine/local_training_worker.py`
- Create: `backend/frozen_entry.py`
- Modify: `scripts/build_backend_binary.py`
- Test: `backend/tests/test_service_s1_09.py`

**Interfaces:** `OwnedProcessIdentity(pid, create_time, token) / platform_launcher.start(spec)`

**Consumes:** S1-02, S1-04

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- Windows spawn/frozen entrypoint/DataLoader/경로 공백·한글/권한 제한에서 실행·종료·재시작이 동작한다.
- 자식 process tree만 종료하며 다른 프로세스에 재사용된 PID는 종료하지 않는다. GPU pack 없는 CPU 설치에서도 실행된다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s1_09.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s1_09.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S1-10 이벤트와 클라이언트 host adapter

**Files:**

- Modify: `src/renderer/services/api.ts`
- Modify: `src/renderer/services/websocket.ts`
- Create: `src/renderer/services/hostAdapter.ts`
- Create: `backend/api/routes_job_events.py`
- Test: `backend/tests/test_service_s1_10.py`

**Interfaces:** `HostAdapter(selectFiles, upload, download, reveal, credentials) / subscribe_events(after_cursor)`

**Consumes:** S1-02, S1-06, S1-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 재연결 후 cursor로 누락 이벤트를 보충하고 UI가 DB 상태와 일치한다. Desktop 및 browser의 file/auth 기능을 명시적으로 adapter 분리한다.
- 향후 브라우저 클라이언트는 동일 작업 UI·upload/download로 연결한다. native camera/폴더 감시는 companion/runtime를 사용한다.
- OpenAPI와 공통 contracts에서 TS client 타입을 생성해 schema drift를 검증한다. domain/engine은 API singleton을 import하지 않는 경계를 단계적으로 정리한다. cursor는 project/stream scoped이고 event ID로 중복을 제거한다. retention으로 cursor가 만료되면 cursor_expired와 authoritative snapshot 재조회·새 cursor를 사용한다. project 전환 시 이전 stream 응답을 적용하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s1_10.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s1_10.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## phase 완료 판정

- 해당 task의 계약·영속·재열기·실패·handoff 근거가 존재한다.
- 새로운 interface가 다음 phase 및 legacy ID와 연결되어 있다.
- 테스트 skip/부족 데이터/미지원 target/서명/실장비 미검증은 이유와 owner를 남긴다.
- 조건이 없는 항목의 `not_required`는 사유와 reviewer가 있어야 하며 pending을 임의 완료로 바꾸지 않는다.

## 추가 기능 acceptance 연결

구현 시 spec 확장 계약과 registry의 acceptance를 함께 사용한다. 다른 phase의 owner가 만든 계약은 검토한 뒤 소비한다.

- E08 데이터 입출력·백업도 같은 영속 작업 센터에서 재개: owner S1-02; 이 phase 연결 S1-02
