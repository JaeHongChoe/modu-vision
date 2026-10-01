# Windows 오픈소스 서비스 고도화 설계

## 목표와 상태

이 문서는 제조 검사 플랫폼을 Windows 중심의 오픈소스 제품으로 고도화하기 위한 검토용 설계다. 개발 규모가 커도 괜찮다는 요청에 따라 기존 기능을 모두 보존하고, 작업 흐름·협업·지속 운영·설치 및 공개 유지보수까지 다룬다. 현재 소스 기준은 `03e8f6d142e6f8e7fa6bd80cb82a4a9ccf8d65e6`이다. 새로운 구현, 버전 변경, 배포 또는 제품 준비 완료를 의미하지 않는다.

사용자가 확정한 조건은 Windows 우선, 오픈소스 공개, 기능과 전체 동선 우선, 5단계 플로우차트 중점, DINOv3 CLS/SEG/Patch 및 YOLO 검출이다. 배포 형태는 개인 로컬 모드와 선택형 고객사 내부 팀 서버를 기본 가정으로 한다. 공개 인터넷 SaaS, 과금, 특정 장비 종속 기능은 별도 배포 요구가 생기면 같은 계약 위에서 확장한다.

## 현재 근거와 해결 순서

현재 기능 범위는 넓고 기존 기록에는 backend 2269 통과/29 skip, renderer 160 통과와 macOS frozen package 및 일부 실제 원격 실행 증거가 있다. 새 평가에서는 아래 핵심 문제가 재현되었다. 테스트 수를 제품 완성도와 동일시하지 않는다. 과거 기능 registry의 integration 상태와 실제 사용 acceptance도 별도로 관리한다.

| 우선 | 확인된 문제 | 범위와 다음 행동 |
| --- | --- | --- |
| 1 | 공간 증강 후 이미지와 bbox/mask 불일치 | 실제 production loader에서 확인. none/photometric 제외. 사용 설정을 확인해 영향받은 모델을 선별 재검증한다. |
| 2 | PaDiM/PatchCore raw score와 플로우 0~1 threshold 불일치 | raw=4/threshold=8의 모델 판정과 node=.5 판정이 달라짐. 실제 전체 NG 비율은 측정하지 않았다. |
| 3 | 비교 후 정답 변경에도 이전 근거로 승인/export 가능 | 격리 API와 실제 CPU parity 경로에서 확인. truth revision 검증을 승인 수명 전체에 적용한다. |
| 4 | 철회된 승인을 중앙 fleet rollback이 재전송 | 전송 mock에서 확인. 실제 현장 적용은 하지 않았다. 새 명령의 현재 eligibility를 공통 검사한다. |
| 5 | UI 버전·실행·선택 상태 연결 한계 | 과거 평가 표시, drag 중 결과 유실, image viewer 연결, failed task 변경을 production UI에서 확인·수정한다. |

로컬 journal/PID 재사용, frozen watcher entrypoint, 무한 재시도, shared HTTPS/CSP, checkpoint/IPC 보안 등은 추가 재현 후보다. 확인 전 확정 결함으로 기록하지 않는다. 현재 작은 실제 데이터 평가의 NG 미검 원인은 분리되지 않았고 정상 정답도 없어 공정 품질은 승인되지 않았다.

## 아키텍처 선택

| 접근 | 장점 | 부담 | 판단 |
| --- | --- | --- | --- |
| Windows 단독 앱 집중 | 설치가 단순하고 개인 시작이 쉬움 | 팀 데이터·공유 GPU·현장 운영 확장에 추가 작업 | 개인 모드로 유지 |
| Windows Studio와 모듈형 API, 선택 팀 서버 및 worker | 기존 엔진·DAG·데이터 출처·패키지를 재사용하고 동일 계약으로 확장 | 영속 작업 원장과 권한·호환성 계약 정리가 필요 | 기본 권장 |
| 전 기능을 분산 서비스로 재작성 | 큰 조직의 독립 배포 가능 | 초기 설치·장애·운영 비용 및 기존 기능 재구현 범위 증가 | 측정된 분리 필요가 생긴 모듈부터 선택 |

권장 구조는 다음과 같다.

```text
Windows Studio ─┐
Browser Client ├─ Local API 또는 Team API
CLI / SDK ─────┘      Project · Dataset · Review · Model · Evaluation · Flow · Release
                     Durable JobStore · Scheduler · Authorization · ArtifactStore
                                    │
                        Local Worker / Linux GPU Worker
                                    │
                       versioned, verified artifacts

Approved Release ── Independent Inspection Runtime
                    Inbox → Execute → Results → Outbox → PLC / MES / API
```

개인 모드는 API/scheduler/local worker를 함께 실행한다. 팀 모드는 같은 API 모듈을 서버에 배치하고 worker를 분리한다. 현장 검사 runtime은 Studio 종료, 학습 부하, 중앙 연결 끊김과 독립적인 수명을 가진다. localhost API도 프로세스 인증과 파일 권한 경계를 유지한다.

React/TypeScript/Electron, Python/FastAPI/PyTorch 기반을 이어간다. 초기 local DB는 SQLite와 관리된 파일 저장소다. 팀 단일 API/scheduler에서 먼저 검증하고 PostgreSQL·S3 호환 adapter는 동시성/데이터 규모 요구에 맞춰 확장한다. SQLite WAL을 NAS에서 여러 호스트가 직접 열도록 하지 않는다. JSON은 기존 프로젝트 import/export 호환에 남기며 DB와 JSON이 동시에 authoritative하지 않게 한다. Redis/message broker/Kubernetes 도입은 실제 필요와 운영 책임을 확인한 뒤 결정한다.

## 도메인 계약

- `ProjectContext`: workspace/project/actor/mode를 요청·이벤트·작업에 명시한다. 프로젝트 선택 UI의 전역 상태에 권한을 의존하지 않는다.
- `ArtifactRef`: ID/revision/hash로 원본·label·mask·split·model·evaluation·flow·package·result를 연결한다. 서버 절대 경로를 클라이언트 식별자로 사용하지 않는다.
- `DatasetSnapshot`: 이미지, annotation, labelbook/guideline, truth, class role, split revision을 고정한다. 빈 라벨은 정상 정답의 증거가 아니다.
- `ScoreSpec`: domain/unit/direction/calibration/threshold를 가진다. 확률·거리·pixel·angle·text 규칙은 공통 0~1 slider로 통일하지 않는다.
- `FlowIdentity`: semantic revision과 layout revision을 분리한다. 위치 이동과 검사 규칙 변경을 구분하며 completed result를 무심코 유실하지 않는다.
- `ApprovalRevision`: subject(model/whole-flow), evaluation/truth/policy/model/graph/device binding을 가진다. approve/export/stage/newapply/centralrollback의 최신 적격성 검사를 공통 구현한다.
- `ReleaseManifest`: 여러 모델·ROI·전처리·rules·score/calibration·runtime·dependency·device·parity·approval 및 package hash를 포함한다. 기록된 ACK와 실제 runtime readback 후 활성화한다.

오래된 근거는 삭제 대신 stale 사유와 다음 행동을 표시한다. 기존 offline runtime의 봉인된 정책과 새로운 중앙 명령의 live 권한/철회 검사는 별도 계약이다. 비상 복귀는 사유·권한·정책과 audit를 가진 명시적인 동작으로 설계한다.

## 작업과 서버 자원

작업 상태는 `queued → preparing → running → verifying → completed`와 `cancel_requested`, `cancelled`, `failed`, `interrupted`, `unknown`을 가진다. 기존 상태의 stopped/disconnected 의미는 migration에서 잃지 않는다. jobs/attempts/events/artifacts/cancellations/reservations/parent-child를 영속 저장하고 attempt별 worker identity와 fencing token을 기록한다.

같은 submit은 멱등하게 처리하고 claim/result publish의 중복을 막는다. 네트워크 현실에서 무조건 정확히 한 번 실행된다는 표현은 사용하지 않는다. 취소 요청, worker ACK, process exit, 자원 해제는 각각 증거가 필요하다. 불확실 remote 작업은 예약을 유지하고 확인 행동을 제공한다. 사용자와 무관한 GPU 작업/PID를 종료하지 않는다.

범용 서버 연결은 hostname/port/user/transport/configured auth를 받고 특정 IP에 종속하지 않는다. SSH/Docker는 첫 transport adapter로 재사용한다. 등록된 worker의 OS/arch/device/task/stage/runtime/weight inventory를 검증한다. 10개 모델군의 train/evaluate/infer/AutoDL/flow/export-preflight가 같은 target 계약을 사용한다. unsupported 조합에는 명확한 이유를 보여주며 local fallback을 숨기지 않는다.

AutoDL은 parent search와 trial child jobs로 운영한다. budget/objective/seed/snapshot이 고정되고 완료 trial은 취소/재시작 후 보존된다. warm-start와 optimizer/scheduler/scaler/RNG/step 기반 resume는 구분한다. DDP는 replica data parallel이다. 학습 속도 튜닝은 기능과 검증을 마친 뒤 측정값으로 다룬다.

## 사용자 경험과 5단계

6단계 데이터→라벨→학습→평가→플로우→검사를 유지한다. 단계마다 준비 상태와 다음 행동을 제공하고 학습·승인·배포의 실행 전제는 검사한다. 기존 결과를 보거나 이전 단계로 이동하는 행동은 사용 목적에 맞춰 허용한다.

5단계는 `편집 / 테스트 / 일괄 평가 / 배포`로 나눈다. 호환 모델 선택기, typed port, class·coordinate·score 단위 검증, image ROI editor를 중심으로 구성한다. fixed ROI·detection ROI·patch·alignment·Blob·class branch·merge·multi-model chain·subgraph template·Undo/Redo·version diff를 빠짐없이 다룬다.

원본/overlay/heatmap/mask/bbox/crop viewer는 label·평가·A/B·flow debugger·inspection/review queue에서 공유한다. 오류 목록의 이미지를 누르면 정확한 source와 run/version/node를 보여주고 복귀 동선을 제공한다. 부분 실행과 전체 검사 결과를 구분하고 branch skip·중간 결과·판정 이유를 표시한다. page/search/filter를 사용해 현재 200개/64개 제한으로 선택이 누락되는 문제를 해결한다.

공통 디자인은 typography·spacing·form·status·error·loading·empty를 통일하고 한국어/영어·키보드·Windows DPI·좁은 화면을 검증한다. 기본 view는 사람이 이해하는 명칭과 주 행동을 보여주고 세부 ID/hash/config는 details에 둔다. operator view는 recipe·input health·검사·REVIEW·실제 active release·장애 복구를 중심으로 한다.

## 데이터와 모델 범위

데이터는 read-only originals, versioned annotations/labelbook/truth/splits, derived images, candidate suggestions, reviewed adoption을 구분한다. LabelMe/COCO/YOLO/mask·지원 DICOM·고해상도 이미지 입출력과 좌표/shape 보존을 검증한다. 태그·제품/Lot·검토 상태·reviewer·guideline version·locks/conflict·작업 이력·중복/준비도·group split·active learning을 연결한다. model/text/image/point/box prompt 및 한국어 keyword 제안은 실제 provider의 후보로 저장하고 사람이 승인한다.

| 모델군 | 기본 또는 확장 방향 | 완료에 필요한 연결 |
| --- | --- | --- |
| CLS | 실제 DINOv3, head-only 기본과 선택 fine-tune | class label→train→평가/threshold→flow→export |
| SEG | 실제 DINOv3 dense decoder/tile | mask→joint augment→train→pixel/Blob평가→flow→export |
| Patch CLS | 실제 DINOv3 | patch/원본group→train→aggregation/heatmap→flow→export |
| Detection | YOLO | 빈 정상/box→train→mAP→검출ROI/체인→flow→export |
| Anomaly | PaDiM/PatchCore/DINO, image/region 목적 구분 | 정상train→valcalibration→heldout평가→flow→export |
| OCR | 현 single-line + detector/recognizer 확장 | text/charset→train→CER/WER→문자rule→flow→export |
| Rotated detection | YOLO OBB 우선 후보, 현재 CNN 이행 | OBB/direction/정상→train→rotatedmetric→ROI→flow→export |
| Rotation/alignment | 학습/기하/template 범위 명시 | angle→train/정렬→좌표복원→평가→flow→export |
| Enhancement | 실제 pair 준비 UI와 synthetic 예제 구분 | pair→train→metric/결함보존→flow→export |
| GAN | 생성/검수 데이터 pipeline | source/mask→train→generate→사람review→train-only채택 및 생성package |

모든 모델에 task별 label validation·실행 위치·취소·재개·checkpoint·runtime/weight license·지원표를 적용한다. GAN을 검사 모델 노드로 위장하지 않는다. 전문 task에 필요한 정답이 없는 데이터를 억지로 완료 증거로 사용하지 않는다. 같은 frozen test cohort의 A/B 및 제품/Lot별 오류, threshold 결과, 재평가 history를 제공한다. validation/calibration에서 맞춘 규칙을 heldout test에 고정한다.

## 현장 운영과 서비스화

headless 검사 서비스는 durable inbox/results/outbox와 bounded retry/dead-letter를 가진다. input은 완료된 파일의 folder watch와 HTTP부터 시작해 USB/RTSP/산업용 SDK adapter로 확장한다. Modbus TCP/HTTP 중심 PLC/MES와 OPC UA 확장 경계를 제공한다. 검사 성공·판정·전송 ACK는 별도 상태다. physical equipment가 없으면 simulator 계약 검증 상태로 공개한다.

제품 recipe와 target fleet은 desired/observed release·device·health·rollout/readback·실패 복귀를 보존한다. Windows 무인 서비스와 Linux daemon은 실제 OS lifecycle을 검증한다. Windows logon task를 SCM service와 동일시하지 않는다. C++/C#/Python/REST/CLI 연동은 실제 필요 runtime을 문서화한다.

persisted structured log·trace·metrics·GPU/queue/disk·error catalog·redacted diagnostics, backup+실제 restore, retention/quota·security/RBAC/audit·offline policy를 운영 기능으로 포함한다. data drift와 REVIEW 비율 변화는 정답이 없는 품질 추정으로 단정하지 않는다. 개인정보 및 외부 telemetry는 기본적으로 외부 전송하지 않는다.

## Windows와 오픈소스 공개

최초 공식 지원 목표는 Windows11 x64 CPU 설치, 선택 NVIDIA local 실행, Windows→Linux GPU worker, Linux headless 검사다. Windows10/macOS/Linux desktop/Edge/NPU/MIG는 조합별 지원·실제 증거를 공개한다. 기본 demo는 외부 다운로드 없이 합법적으로 제공 가능한 작은 CPU 모델/이미지로 실제 실행된다.

Python/Node 없는 Windows에서 NSIS non-admin install·launch/restart·known-image·update failure/recovery·uninstall/reinstall을 검증한다. CPU runtime, optional GPU/기능 pack, 가중치는 각각 inventory/license/hash/compatibility를 가진다. 서명 identity 없는 artifact는 unsigned 개발판이며 signed release gate를 통과한 것처럼 표시하지 않는다.

현재 root LICENSE는 MIT다. 실제 YOLO dependency는 AGPL-3.0이고 DINOv3 가중치는 별도 조건을 가진다. 전체 프로그램의 배포 license와 가중치 재배포를 먼저 결정하며 오픈소스라는 말만으로 모든 가중치를 자유 배포한다고 가정하지 않는다. 모듈을 나눴다는 사실만으로 license 의무 면제를 주장하지 않는다. 법적 조건 검토가 끝나지 않은 pack은 배포하지 않는다. 필요한 third-party 저작권/라이선스 고지는 유지하고 불필요한 비교/홍보 회사명과 비밀정보를 공개 자료에서 제거한다.

원문 확인 근거: [YOLO dependency license](https://github.com/ultralytics/ultralytics/blob/main/LICENSE), [배포자 license 안내](https://www.ultralytics.com/license), [DINOv3 license](https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md), [현재 사용 모델 card](https://huggingface.co/timm/vit_small_patch16_dinov3.lvd1689m). 이는 release 정책 결정의 입력이며 이 문서가 최종 license 호환 판정은 아니다.

공개 저장소에는 source/lock·architecture·Windows quickstart·demo·API/SDK 예제·CONTRIBUTING/SECURITY·issue/PR templates·지원표·CI·SBOM·release checklist를 포함한다. 외부 PR의 CI에는 secrets/서버 접속을 제공하지 않는다. signed release는 보호된 workflow로 만든다. source와 binary receipt를 연결하며 bit-for-bit 재현성은 실제 측정 뒤에 표시한다.

## 검증과 출시 단계

완료 상태는 planned/implemented/contract_verified/native_verified/real_input_verified/accepted로 나누고, 품질 승인·서명·장비 검증을 별도 gate로 기록한다. 이전 123 F 항목과 33 U 항목은 새 80 작업 묶음과 연결하여 신규 기능 추가 중 기존 기능이 사라지지 않게 한다. 각 항목은 GUI/persist/reopen/failure/handoff 및 platform/target evidence가 필요하다.

| milestone | 필수 gate |
| --- | --- |
| Integrity baseline | 네 재현 결함 차단, UI 상태 결함 회귀, 영향 조사 |
| Windows alpha | 실제 CPU 설치/예제, 영속 작업, 5단계 편집·viewer·버전 흐름 |
| Team beta | 다중 사용자 권한/편집, remote 전 task/AutoDL, data lifecycle·backuprestore |
| Service candidate | headless/queue/ACK/fleet, offline/update/restore, SDK 및 운영 진단 |
| Public release | 지원표의 실제 Windows 기능 증거, critical0, license/source/SBOM/문서/서명 상태 및 알려진 한계 공개 |
| Field approval | 공정별 검토 정상/불량 cohort와 사람 품질 승인, 실제 장비 contract/택트/연속운전 증거 |

72시간 soak, 10k/100k metadata와 고해상도 원본은 계획상 목표 시험이다. 실제 지원 규모·속도·SLA는 target 측정 후 결정한다. 기능 공개 release와 특정 공정 투입 승인은 별개의 결과다.

## 개발 분담과 검토

진행은 무결성/공통계약→플랫폼 기반→UX·데이터·모델 병렬→현장/배포→통합검증으로 한다. Windows build/license/CI 발견 작업은 초반부터 병행한다. API와 schema를 담당하는 통합 owner를 지정하고 file ownership 없이 같은 router/store를 동시에 수정하지 않는다.

실행 전 phase별 계약을 읽고 failing behavior test→최소 구현→focused 확인→독립 review→integration gate→commit 순서로 진행한다. UI 항목은 자동 assertion과 실제 native 조작 증거를 함께 기록한다. 대규모 GPU sweep, 타 작업 취소, production 설치 변경은 이 계획에 포함된 실행 권한으로 간주하지 않는다.

이번 문서는 검토용 설계와 실행 계획을 함께 요청한 것에 대한 산출물이다. 구조를 바꾸는 구현은 이 설계와 차수별 계획 검토 후 시작한다. 일정은 실제 인력·Windows target·데이터·signing·license 결정이 준비된 뒤 phase별로 산정한다.

## 구현 중 기능 확장 기준 (2026-10-02)

기존 82개 작업을 유지하고 아래 8개 기능 계약을 담당 작업의 acceptance에 추가한다. 기존 여러 모델 연결·각도 정렬·검수 투표와 중복 계산하지 않는다. 기능 구현 상태는 registry와 실제 실행 근거로 판단한다. 공개 기능 조사에서 확인한 사용 목적을 자체 계약으로 설계했으며, 다른 제품의 코드·비공개 UI·전용 SDK를 복제하지 않는다.

| ID | 기능 | 구현 책임 | 연결 작업 |
| --- | --- | --- | --- |
| E01 | 한 부품의 여러 view를 묶는 입력 계약 | S5-03 | S5-03, S5-05, S2-05 |
| E02 | 기준 특징에 고정되는 fixture ROI | S2-05 | S2-05, S4-08 |
| E03 | 실제 치수 교정 artifact와 설정 일치 검사 | S3-08 | S2-05, S5-05, S3-08 |
| E04 | 검사 규칙 변경의 사유와 before/after 감사기록 | S5-10 | S5-10, S5-07, S2-08 |
| E05 | 정답 기준 라벨 검수와 불일치 위치 표시 | S3-07 | S3-04, S3-07 |
| E06 | 현장 수집량을 제한하는 sample 정책 | S5-08 | S3-09, S5-08, S5-09 |
| E07 | 배포 준비 화면의 전체 의존성 점검표 | S5-05 | S5-05, S6-03, S2-04, S6-10 |
| E08 | 데이터 입출력·백업도 같은 영속 작업 센터에서 재개 | S1-02 | S1-02, S2-09, S3-01, S3-10, S5-09 |

### E01 한 부품의 여러 view를 묶는 입력 계약

계약: `CaptureGroup(part_id, trigger_id, required_view_ids, frame_refs, timestamp_basis, max_skew_ms, deadline_ms, completeness_policy); one-image DAGs stay usable as view subflows; group joins emit explicit COMPLETE/INCOMPLETE/EXPIRED and one whole-part verdict.`

- Simulator reorders two views of part A and interleaves part B without cross-part mixing.
- Duplicate trigger/view is idempotent; missing view expires to REVIEW/INCOMPLETE; late frame cannot turn an expired group into OK.
- Restart preserves pending group/deadline and outputs; export/reopen retains join policy; Windows native simulator proof precedes camera claims.

### E02 기준 특징에 고정되는 fixture ROI

계약: `FixturePose(reference_artifact_ref, observed_to_reference_transform, coordinate_space, match_quality, residual, ambiguity, valid_region); downstream ROI declares fixture reference and quality bounds. Initial provider can be OpenCV template/keypoint matching, with rigid/affine scope explicit.`

- Synthetic rigid translations/rotations place the same reference ROI at correct source pixels and restore masks/bboxes to source.
- Repeated texture/occlusion/insufficient features route to REVIEW rather than arbitrary fixture.
- Template revision invalidates dependent evidence; package round-trip and Windows CPU execution retain identical transforms.

### E03 실제 치수 교정 artifact와 설정 일치 검사

계약: `SpatialCalibration(ref, method, camera_id, acquisition_config_hash, source_size, reference_artifact_hash, units, scales_or_mapping, residual, valid_plane, approved_at); current simple scale method remains explicit manual planar calibration; lens/perspective rectification optional adapter.`

- Known-length planar fixture verifies within declared tolerance.
- Identical dimensions with changed camera/setup/calibration revision cannot silently retain physical-unit eligibility.
- Crop/resize/rotate carry transform metadata; length/area use correct coordinate space; no evidence keeps units px.
- Flow/package/reopen retain calibration hash; missing artifact blocks physical-unit claim.

### E04 검사 규칙 변경의 사유와 before/after 감사기록

계약: `ConfigurationChange(actor_from_session, subject, parent_revision, next_revision, semantic_delta, reason, time, action, observed_runtime_release); immutable audit outbox tied transactionally to saved change; layout-only edits distinguished.`

- Rule save captures authenticated actor, old/new semantic values and reason; client-supplied authorship cannot forge actor.
- Concurrent save conflict records no successful change; storage failure cannot produce a success audit without committed config.
- Layout move has no inspection-rule approval invalidation; semantic rule change does.
- Current active runtime stays accurately displayed until verified apply ACK.

### E05 정답 기준 라벨 검수와 불일치 위치 표시

계약: `AnnotationQualityProfile(reference_snapshot, guideline_revision, class_match, shape_metric, tolerance, scope); QualityReport(annotation_revision, reference_revision, conflicts, limitations). Begin with approved gold samples and object-level comparison; independent replica editing is a separate optional extension.`

- Known missing/extra/class/geometry disagreement fixtures produce stable conflicts and image links.
- Changing gold label or guideline stales prior report and approval eligibility.
- Gold samples are excluded from ordinary training/test exports unless explicit dataset policy says otherwise; no leakage from QA reference into test truth.
- Unsupported task/shape types explicitly rejected; report persists/reopens.

### E06 현장 수집량을 제한하는 sample 정책

계약: `IntakeSamplingPolicy(revision, seed, eligibility_reasons, score_spec, per_product_lot_camera_quota, max_items_bytes_per_window, normal_baseline_fraction); SamplingReceipt(run_ref, selected_or_skipped, reason, policy_ref). Human review/adoption remains separate.`

- Fixed seed/window produces repeatable selection receipts; duplicate events do not consume budget twice.
- High-volume stream respects item/byte quotas and retains a stated ordinary-input baseline; low-confidence scores from different domains are not compared untyped.
- Quota exhaustion/retention failure is visible; selections retain run/node/recipe origins and never become test truth automatically.

### E07 배포 준비 화면의 전체 의존성 점검표

계약: `RecipeRequirement(node_id, kind, artifact_ref, version_range, platform, license_ref, state, evidence_ref, remedy); PreflightReport(recipe_release, target_identity, requirements, checked_at, environment_hash). Hash verification, process launch and approved target execution stay distinct.`

- Missing calibration/model/custom adapter/optional pack maps to exact node and remedy instead of first generic error only.
- One unavailable optional runtime blocks affected path without fake fallback; unaffected historical results remain viewable.
- Target change or pack/version change invalidates old preflight; report persists, reopens and is available from exported package CLI.

### E08 데이터 입출력·백업도 같은 영속 작업 센터에서 재개

계약: `DataOperation(kind, source_snapshot, expected_target_revision, progress_unit, attempt, staged_output, result_ref, expires_at); publish target mutation only after verification and explicit acceptance appropriate to operation. Restore-to-new-directory first; same JobStore, not an independent queue.`

- Closing/reopening UI retains import/archive job identity and progress.
- Cancellation of scanning/staging leaves no published partial dataset; restore failure retains original project and owned staging evidence.
- Replayed submit is idempotent; output count/hash receipt and expiry displayed; expired result is not shown downloadable.
- Windows file-lock/path/unicode behavior tested on native target.
