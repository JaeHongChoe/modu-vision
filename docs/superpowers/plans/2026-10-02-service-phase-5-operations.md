# 검사 서비스와 현장 운영 실행 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. 이 문서는 검토용이며 새로운 구현 완료 상태가 아니다.

**Goal:** 작업 화면 종료와 네트워크 장애에도 검사·결과 보존·전송·복구를 지속한다.

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

## S5-01 독립 검사 서비스

**Files:**

- Modify: `backend/engine/inspection_service.py`
- Modify: `backend/engine/service_runtime.py`
- Modify: `backend/api/routes_runtime_services.py`
- Test: `backend/tests/test_service_s5_01.py`

**Interfaces:** `InspectionService(inbox, runtime, results, outbox) / InspectionId`

**Consumes:** S1-02, S1-09, S4-14

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- Studio 종료 후 Windows service/Linux daemon에서 검사한다. 서비스 시작·정지·재부팅·해당 사용자권한·GPU warmup·readiness를 구분한다.
- 같은 input id의 중복 요청 결과 게시를 제어하고 전원 중단 후 inbox/results/outbox를 복구한다. 시간 초과·미실행은 OK가 아니다.
- Studio non-admin 설치와 SCM 서비스 등록을 분리한다. SCM은 명시적 elevation·전용 service account·ProgramData ACL·project/network credentials를 검사한다. 사용자 로그인이 없는 reboot/Session0에서 GPU/camera/network 접근을 실제 검증한다. 등록 거절 시 개인 Studio는 유지한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s5_01.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s5_01.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S5-02 폴더와 HTTP 입력

**Files:**

- Modify: `backend/engine/inspection_service.py`
- Create: `backend/engine/input_adapters.py`
- Modify: `backend/api/routes_inspections.py`
- Test: `backend/tests/test_service_s5_02.py`

**Interfaces:** `InputAdapter.receive() -> InspectionInput / submit(input, idempotency_key)`

**Consumes:** S5-01

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 폴더 파일 완료 감지·부분 write·동명·손상·중복·burst·HTTP 재전송을 처리한다. bounded queue/backpressure·timeout·dead-letter·수동 재처리를 제공한다.
- 오류 무한 retry를 막고 재처리 횟수·원인·입력 hash·검사 ID를 보존한다. 결과 CSV/JSON과 API 조회를 지원한다.
- 입력 접수 시 recipe revision·flow release·device·product identity를 고정한다. recipe 전환 시 기본 backlog는 접수 당시 release로 처리하고 운영자가 명시한 drain/reject는 기록한다. A접수→B전환→A실행의 result/PLC ACK가 A 버전으로 연결된다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s5_02.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s5_02.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S5-03 카메라와 video adapter

**Files:**

- Create: `backend/engine/capture_adapters.py`
- Modify: `backend/engine/service_runtime.py`
- Extend directory: `src/renderer/components/inference`
- Test: `backend/tests/test_service_s5_03.py`

**Interfaces:** `CaptureAdapter(capabilities, frame, trigger, reconnect) -> FrameRef`

**Consumes:** S5-01, S1-05

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 범용 USB/RTSP부터 frame capture·trigger·timestamp·camera id·reconnect·dropped frame를 기록한다.
- 산업용 SDK adapter 인터페이스와 simulator를 제공한다. 실제 장비/SDK가 없는 adapter는 계약 검증 상태이며 live hardware 완료로 표시하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s5_03.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s5_03.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S5-04 PLC와 MES 범용 연동

**Files:**

- Create: `backend/engine/plc_adapters.py`
- Create: `backend/engine/mes_adapters.py`
- Extend directory: `src/renderer/components/inference`
- Test: `backend/tests/test_service_s5_04.py`

**Interfaces:** `DeliveryAdapter.send(InspectionResult, correlation_id) -> DeliveryAck`

**Consumes:** S5-01, S5-02

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- Modbus TCP·HTTP 중심 config/form·register/tag mapping·byte order·trigger·timeout·ACK·retry·idempotency를 제공하고 OPC UA adapter 경계를 정의한다.
- 수신 simulator 계약 테스트와 실장비 acceptance를 분리한다. 판정 결과와 전송 성공을 따로 저장하며 수신 거절/단절 상태를 운영 화면에 표시한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s5_04.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s5_04.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S5-05 제품 recipe와 traceability

**Files:**

- Modify: `backend/engine/service_runtime.py`
- Modify: `backend/engine/flow_provenance.py`
- Modify after S2-10 handoff: `src/renderer/components/operator/OperatorWorkspace.tsx`
- Test: `backend/tests/test_service_s5_05.py`

**Interfaces:** `ProductRecipe(product, lot_schema, flow_release, device, input_spec)`

**Consumes:** S5-01, S3-03, S2-10

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 제품/Lot/설비/camera·검사 ID·원본·모델·flow·rule·버전·시간·operator를 결과에 연결한다.
- recipe 전환 시 적용 ACK 전까지 이전 활성 버전을 표시한다. 동일 부품의 재검사/검토 결과를 이전 기록과 연결하고 필터/내보내기를 검증한다.
- 실제 부품 barcode/correlation id·순차/병렬 모델 실행·inspect deadline·batch 결과 history와 원본/prediction JSON을 보존한다. 사람 재판정은 원모델 결과를 덮어쓰지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s5_05.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s5_05.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S5-06 장비 fleet와 단계 배포

**Files:**

- Modify: `backend/engine/fleet.py`
- Modify: `backend/engine/fleet_agent.py`
- Modify: `backend/api/routes_fleet.py`
- Test: `backend/tests/test_service_s5_06.py`

**Interfaces:** `FleetTarget(desired_release, observed_release, capability, health) / RolloutPlan`

**Consumes:** S0-04, S1-05, S4-14, S5-01

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- target 등록·live health·실제 활성 hash·단계 배포·소수 target 검증·일시중지·실패 복귀를 구현한다.
- 새deploy/rollback은 현재 eligibility, targetdevice,packagehash,수신ACK/readback을 모두 확인한다. 중앙 연결 끊김과 기존 offline 가동 정책은 명시적으로 구분한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s5_06.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s5_06.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S5-07 관측과 운영 알림

**Files:**

- Create: `backend/engine/observability.py`
- Modify: `backend/engine/inspection_service.py`
- Modify after S1-02 handoff: `backend/engine/job_store.py`
- Create: `src/renderer/components/operations`
- Test: `backend/tests/test_service_s5_07.py`

**Interfaces:** `TelemetryEvent(trace_id, job_id, run_id, node_id) / HealthSnapshot`

**Consumes:** S1-02, S5-01

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- persisted structured logs·metrics·trace·GPU/queue/disk·error catalog·health/readiness를 작업과 검사 ID로 연결한다.
- UI log는 전체 영속 로그의 페이지이다. 개인정보/secret을 redaction한다. opt-in 알림은 변화·실패·필요행동에만 발생하며 외부 telemetry는 기본 off이다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s5_07.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s5_07.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S5-08 data drift와 개선 반복

**Files:**

- Modify: `backend/engine/workflow_impact.py`
- Modify: `backend/engine/capture_intake.py`
- Modify after S5-07 handoff: `src/renderer/components/operations`
- Test: `backend/tests/test_service_s5_08.py`

**Interfaces:** `DriftReport(reference_snapshot, incoming_window, limitations)`

**Consumes:** S3-09, S4-13, S5-05, S5-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 제품/Lot별 입력 분포·REVIEW/NG 비율·human feedback 변화와 기준 모델 버전을 표시한다.
- 정답 없는 NG비율 변화를 실제 품질 하락으로 단정하지 않는다. intake→검수→retrain→동일cohort 비교→사람승인→배포로 연결한다.
- 정책 기반 자동 재학습·재배포는 opt-in이다. 기본은 candidate 생성 후 사람 승인이다. 자동 promotion은 사전 승인된 policy revision·fresh truth/heldout 기준·패키지/장치/현재 적격성·audit와 실패 rollback을 모두 만족해야 하며 검증 전 지원 완료로 표시하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s5_08.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s5_08.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S5-09 백업과 복구 및 보존기한

**Files:**

- Create: `backend/storage/backup.py`
- Modify: `backend/engine/project_archive.py`
- Modify: `backend/engine/inspection_service.py`
- Test: `backend/tests/test_service_s5_09.py`

**Interfaces:** `BackupManifest(db_revision, artifacts, audit_cursor) / restore_backup(...)`

**Consumes:** S1-08, S3-10, S5-01

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 일관된 DB+artifact+승인+runtime config backup을 만들고 별도 빈 환경에 실제 복구한다. 보존기한·quota·archive/trash를 설정한다.
- backup 성공과 restore 성공을 별도 기록한다. 활성 release와 법적/운영 보존 대상 artifact를 정리 작업에서 보호한다.
- backup은 객체 참조 pin과 일관된 DB snapshot을 묶으며 concurrent GC/retention 중에도 빈 환경에서 참조를 완전히 복구한다. cutover 후 정상 데이터를 오래된 backup으로 덮는 updater 재시도를 거절한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s5_09.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s5_09.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S5-10 서비스 보안과 운영 설정

**Files:**

- Modify: `backend/api/shared_authorization.py`
- Modify: `backend/engine/fleet_agent.py`
- Modify: `backend/engine/service_runtime.py`
- Modify: `src/main/index.ts`
- Test: `backend/tests/test_service_s5_10.py`

**Interfaces:** `ServiceSecurityPolicy(tls, tokens, paths, origins, actions) / AuditEvent`

**Consumes:** S1-07, S5-06

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- HTTPS·worker/fleet 인증·세션 만료·project isolation·업로드/archive 경로 traversal·checkpoint trust·IPC allowlist·external URL allowlist를 검증한다.
- 비밀정보는 OS credential store/서버 secret 설정으로 관리한다. 승인/적용/권한변경 감사기록과 정책 version을 보존하며 developer-mode 사용을 명확히 표시한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `python -m pytest backend/tests/test_service_s5_10.py -q`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `python -m pytest backend/tests/test_service_s5_10.py -q`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## phase 완료 판정

- 해당 task의 계약·영속·재열기·실패·handoff 근거가 존재한다.
- 새로운 interface가 다음 phase 및 legacy ID와 연결되어 있다.
- 테스트 skip/부족 데이터/미지원 target/서명/실장비 미검증은 이유와 owner를 남긴다.
- 조건이 없는 항목의 `not_required`는 사유와 reviewer가 있어야 하며 pending을 임의 완료로 바꾸지 않는다.

## 추가 기능 acceptance 연결

구현 시 spec 확장 계약과 registry의 acceptance를 함께 사용한다. 다른 phase의 owner가 만든 계약은 검토한 뒤 소비한다.

- E01 한 부품의 여러 view를 묶는 입력 계약: owner S5-03; 이 phase 연결 S5-03, S5-05
- E03 실제 치수 교정 artifact와 설정 일치 검사: owner S3-08; 이 phase 연결 S5-05
- E04 검사 규칙 변경의 사유와 before/after 감사기록: owner S5-10; 이 phase 연결 S5-10, S5-07
- E06 현장 수집량을 제한하는 sample 정책: owner S5-08; 이 phase 연결 S5-08, S5-09
- E07 배포 준비 화면의 전체 의존성 점검표: owner S5-05; 이 phase 연결 S5-05
- E08 데이터 입출력·백업도 같은 영속 작업 센터에서 재개: owner S1-02; 이 phase 연결 S5-09
