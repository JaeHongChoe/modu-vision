# Modu-vision 작업표

확인일: 2026-10-08 · 실제 실행 소스는 각 receipt에 고정한다. 영속 실행 controller·실제 앱 검사는 `1a9b830`, 최신 장애 matrix는 `213e9b7`, 이전 설치/이력 검사는 `369b8e1`이다. 게시 HEAD와 hosted CI 완료는 별도로 읽는다. 아래 후속 기록은 당시 집계이며 최신 숫자는 `scripts/remaining_work_report.py`로 확인한다.

**82개 중 소프트웨어 구현 확인69개, 구현 pending13개.** Windows 전용 S6-02·S7-03은 사용자 요청 범위에서 면제하므로 계속 필요한 구현 부모는11개다. 면제는 테스트 통과가 아니며 원래 수락 gate를 유지한다. 최종 수용 승인은0개다. 부모 pending에는 남은 코드와 서명·장비·사람 검증·실제 경과 시간 조건이 함께 들어 있다. S1-08·S7-04 구현 완료도 나머지8개 수락 차원의 통과를 뜻하지 않는다.

## 실행 순서

- S7-05의72시간 검사는 외장 저장소의 기존 실행을 계속 유지하고, 완주 판정은 마지막에 확인한다. 같은 실행의 소스·모델·시작 시각을 바꾸거나 이전 실행 시간을 합산하지 않는다.
- 독립적으로 가능한 구현·CI·앱 실행 검증은 종료를 기다리지 않는다. 현재 actual CPU 기준 이미지 실행, S7-01 화면의 실패·취소·전달 시나리오와 공개 CI 실패 원인을 병행한다.

## 2026-10-08 실행 잠금과 종료 기록 후속 작업

| 소스 | 이번에 구현하고 확인한 범위 | 계속 남은 작업 |
|---|---|---|
| `85600b7` | 원래 controller가 backend를 실행 전에 등록하고, backend/main의 비공개 채널로 작업 접수 중단과 종료 결과를 확인한다. writer core·제어 경로22개, Node 종료10개 검사 통과. | 전체 background·local·remote writer 등록, 원래 Node child authority와 core 연결, 전체 process-tree 종료·설치 adapter. |
| `9b74433` | 실행별 임시 홈을 일반 사용자 데이터 inventory·hash·기존 home 인수 대상에서 제외한다. 실제 경로·symlink 거절을 포함한 관련94개 검사 통과. 기존 active DB 이관은 이미 선언된 scope만 복사했으며, 이번 수정으로 새 DB 복사 문제를 해결했다고 주장하지 않는다. | 실제 설치 home·운영 장비 및 전체 수락. |
| `0c6a77e` | 원래 CPU writer 잠금의 private duplicate를 실제 worker와 남은 descendant에 전달하고, 결과 검증·정리까지 producer lifetime을 유지한다. 종료 결과 게시의 짧은 transition contention만 기존4초 안에서 재검증·재시도한다. 기존 lease와 새 실패 검사46개, 실제 CPU·drain·원래 Node/backend exit 통합49개 통과. | core의 backend row는 reserved, CPU row는 unsupported로 유지한다. 전체 writer/tree 종료나 launch lease 해제는 아직 승인하지 않는다. |
| `56effc7` | 검증한 writer·임시 홈·CPU lifetime·drain 실패 검사6개 파일을 source CI에 추가한다. 기존 queue·권한·timeout은 유지한다. | 정확한 최신 게시 소스의 전체 hosted CI 결과. |
| `61ddadf` | 원래 Popen의 시작 직후 빈 실행 명령을 영속 identity로 기록하지 않는다. 최초 birth·parent·session과 원래 handle을 유지하고0.5초 안에서 같은 비어 있지 않은 명령을 두 번 확인한다. 새 실패·복구 검사와 기존 종료·spawn 실패15개 통과. 실제 Linux의 소스`0079355`에서도 원래 inspector와 신규 시작·metadata16개 검사가 통과했다. | 기존 잘못된 이력은 자동 수정하지 않는다. 전체 writer 종료 확인과 최신 전체 hosted CI. |
| `2d20247` | 선택된 CPU 플로우의 package 검사·임시 파일 작성 전에 원래 writer admission을 얻고, 실제 실행·결과 해석·정리까지 유지한다. protocol2 거절,3의 counted scope,4의 원래 descriptor 전달과 독립 SDK를22개 검사로 확인했다. SDK fixture 보정 후 관련6개도 통과. | inline·GPU·원격·background producer 및 전체 수락은 별도 작업이다. |
| `0079355` | inert compiled-intent fixture의 Darwin schema2와 Linux schema1을 구분해 원래 crash-before-spawn·재시도 거절 검사를 유지한다. metadata 검사10개 통과. | 실제 compiled/native·publisher 실행을 이 fixture로 승인하지 않는다. |
| `ecf41e4` | 시작 identity와 CPU 플로우 검사2개 파일을 source CI에 추가한다. selector227개 중복·누락 없음을 확인했다. | 새 게시 소스의 전체 hosted CI 완료. |

각 gate는 실행 당시 dirty source의 전후 manifest와 정확한 변경 파일 hash에 연결한다. 이 결과를 후속 소스의 clean commit·frozen/native·서명·공정 품질 수락으로 확대하지 않는다. 상세 근거는 `docs/verification/receipts/2026-10-08-original-writer-drain-0c6a77e.json`과 `docs/verification/receipts/2026-10-08-flow-start-0079355.json`이다. 후자의 합친 소스 검사58개는 실패·skip 없이 통과했고2219개 source 입력 hash가 전후 같았다. 부모 구현 집계69/13과 curated658/791은 유지한다. 남은 producer·Linux 영향 검증·UI 시나리오는 계속 진행한다.

Linux focused16개 실행은 GPU·network 없이 고정된 CPU container에서 실행했고`0079355` Git archive2219개 파일과 실행 전후 manifest가 일치했다. 이전에 실패한 inspector의 원래 assertions와10초 timeout을 유지해`starting`과 원래 child의 birth·명령 hash를 확인했다. 정리는 비공개 원래 Popen의 terminate/wait로 한정했으므로 원래 teardown 동등성이나 전체 tree·lease 해제를 승인하지 않는다. 이 기록은 `docs/verification/receipts/2026-10-08-linux-start-0079355.json`이며, 정확한 최신 전체 hosted CI는 별도 대기 중이다.

## 단계별 집계

| 단계 | 전체 | 구현 확인 | 미완료 |
|---|---:|---:|---:|
| S0 | 9 | 9 | 0 |
| S1 | 10 | 10 | 0 |
| S2 | 10 | 10 | 0 |
| S3 | 10 | 10 | 0 |
| S4 | 14 | 14 | 0 |
| S5 | 10 | 9 | 1 |
| S6 | 11 | 6 | 5 |
| S7 | 8 | 1 | 7 |

## 원래 implementation pending13개 · 요청 범위11개

| ID | 작업 | 실제 남은 조건 |
|---|---|---|
| S5-01 | 독립 검사 서비스 | 독립 서비스의 SCM 등록·권한·Session0·재부팅과 실제 장치 검증 필요. |
| S6-02 | Windows CPU 설치 프로그램 | 현재 Windows unsigned NSIS/portable 빌드·CPU 패키지 실행은 통과. non-admin 설치·제거·SCM 분리와 실제 설치 QA는 미검증/면제. |
| S6-03 | 선택형 GPU와 runtime pack | pydicom·OpenVINO·CUDA12·CTC의 개별 범위 근거를 유지한다. Studio의 프로젝트별 비활성 설치·별도 pin·실제 변조 거절·재시도·재열기는 clean5284df6 browser2/native3, backend83, renderer907개로 검증했다. 자동 활성화·frozen/native 배포 조합·실 publisher·대표 품질은 별도 조건이다. |
| S6-04 | 오프라인 설치와 업데이트 | POSIX 앱/DB cutover·복구·기존 home 인수, 영속 controller와 main/backend 인증 handshake를 구현하고 검증했다. 원래 backend epoch의 source CPU 기준 이미지 실행과 source candidate 활성 pointer 변경 전 검사·복구를 추가 검증했다. 정확한 compiled candidate worker의 A/OK 수학·두 pointer 변경 전 봉인·재실행 거절과 소스78b6b73의 실제 private frozen 앱 시작을 검증했다. 후속 소스 변경은 이 frozen 결과를 승계하지 않는다. 전체 writer/process-tree 종료 확인, OS installer adapter와 실제 publisher 서명은 계속 미완료이다. |
| S6-05 | 공개 CI와 source 재현성 | 이전 소스의 hosted 성공·실패를 보존한다. 보호된 수동 candidate 검증 workflow와 source/artifact/환경 정책 gate를 추가했다. 실제 workflow 실행·서명·배포는 하지 않았고, 새 게시 소스의 전체 hosted 결과가 필요하다. |
| S6-06 | 서명과 릴리스 채널 | 실제 Authenticode/publisher identity·서명 후 byte inventory·채널 승인 필요. |
| S7-01 | 기존 기능 전체 coverage 계약 | 원래156개 ID를 유지한다. 현재207개 curated action의692개 시나리오 검증,757개 pending이다. 알려진 누락 메뉴/shortcut·남은 시나리오·전체 기능 수락을 이어간다. 행동 수와 전체 기능 완료 수는 다르다. |
| S7-02 | 10모델군 실제 작업 시나리오 | 실제5 distinct learned chain은 완료. 10모델군 각각 사람이 검토한 정답과 대표 multi-task recipe 검증 필요. |
| S7-03 | Windows 실제 설치와 사용 QA | Windows 실제 설치·사용 QA 사용자 면제. hosted Server2025 component 실행을 Win11 실사용 성공으로 세지 않음. |
| S7-05 | 데이터 규모와 연속 운전 | 실제 browser/native에서10만 메타데이터·실제 이미지3장,3×120 keyset page·최대DOM32·응답120·tail UUID/SHA 재열기·파일 없는 항목 선택 거절을 검증했다. 10만 실제 사진 decode나 target 자원/택트 수락은 아니다. a184b1e의 동일 외장72시간 실행은 유지 중이며 이전 시간을 합산하지 않고 terminal receipt가 필요하다. |
| S7-06 | 공정 품질 승인과 장비 검증 | 사용자 지정 공정 미검/과검 정책·대표 truth·카메라/PLC/MES 실제 장비 승인 필요. |
| S7-07 | 공개 후보와 릴리스 판정 | 실제 DICOM 지원 조합과 source-bound 개발 의존성590개/미해결53개·6개 배포 범위 hold 판정 기록. 실제 shipped source/SBOM/notices·서명·독립 검토·공정 품질·첫 사용자 파일럿은 미완료. 개발 환경 inventory로 배포 증거를 대신하지 않음. |
| S7-08 | 파일럿 feedback과 지속 유지 | 첫 사용자의 설명 없는 파일럿 및 실제 운영 책임·지원/SLA/유지보수 주기 필요. |

## 소프트웨어 구현 확인69개

| ID | 작업 |
|---|---|
| S0-01 | 공간 증강의 이미지와 라벨 동시 변환 |
| S0-02 | 이상 점수와 임계값의 단위 통일 |
| S0-03 | 변경된 정답과 오래된 승인 차단 |
| S0-04 | 중앙 배포와 rollback의 공통 적격성 검사 |
| S0-05 | 플로우 실행과 화면 상태 결함 수정 |
| S0-06 | 복구와 보안 후보의 재현 및 판정 |
| S0-07 | 기존 모델과 승인 결과의 영향 조사 |
| S0-08 | 핵심 결함 회귀와 기준 기록 |
| S1-01 | 프로젝트와 실행 문맥 공통 계약 |
| S1-02 | 영속 작업 원장과 멱등 제출 |
| S1-03 | 스케줄러와 작업 소유권 |
| S1-04 | 취소와 장애 상태 통합 |
| S1-05 | 범용 worker 등록과 지원표 |
| S1-06 | 저장소와 artifact 게시 |
| S1-07 | 계정과 프로젝트 권한 |
| S1-08 | 기존 JSON과 DB migration |
| S1-09 | Windows 프로세스와 실행 entrypoint |
| S1-10 | 이벤트와 클라이언트 host adapter |
| S2-01 | 첫 실행과 GPU 없는 예제 |
| S2-02 | 공통 디자인 시스템 |
| S2-03 | 6단계 진행과 준비도 안내 |
| S2-04 | 5단계 편집 화면 재구성 |
| S2-05 | typed DAG와 검사 규칙 편집 |
| S2-06 | 공통 원본과 판정 viewer |
| S2-07 | 대용량 이미지 선택과 검색 |
| S2-08 | 디버거와 A/B 및 버전 이력 |
| S2-09 | 학습과 작업 센터 공통 화면 |
| S2-10 | 운영자 화면과 개인 설정 |
| S3-01 | 데이터 index와 import |
| S3-02 | dataset snapshot과 출처 |
| S3-03 | 태그와 명시적 정답 |
| S3-04 | 팀 라벨 편집과 충돌 처리 |
| S3-05 | 라벨 도구 기능과 format 왕복 |
| S3-06 | 모델과 프롬프트 라벨 제안 |
| S3-07 | 제품과 Lot 분할 및 준비도 |
| S3-08 | 비파괴 편집과 재학습 영향 |
| S3-09 | 검사 수집과 active learning |
| S3-10 | 프로젝트 수명과 데이터 이동 |
| S4-01 | DINOv3 Classification recipe |
| S4-02 | DINOv3 Segmentation recipe |
| S4-03 | DINOv3 Patch Classification recipe |
| S4-04 | YOLO 일반 검출 recipe |
| S4-05 | 이상탐지 두 목적과 calibration |
| S4-06 | OCR 검출과 인식 recipe |
| S4-07 | 회전 검출 production adapter |
| S4-08 | 회전과 정렬 recipe |
| S4-09 | 이미지 개선 실제 pair recipe |
| S4-10 | GAN 생성과 사람 검토 |
| S4-11 | 모든 작업의 실행 위치 통합 |
| S4-12 | AutoDL과 checkpoint 재개 |
| S4-13 | 동일 cohort 모델과 전체 flow 평가 |
| S4-14 | 모델 및 전체 flow 승인 |
| S5-02 | 폴더와 HTTP 입력 |
| S5-03 | 카메라와 video adapter |
| S5-04 | PLC와 MES 범용 연동 |
| S5-05 | 제품 recipe와 traceability |
| S5-06 | 장비 fleet와 단계 배포 |
| S5-07 | 관측과 운영 알림 |
| S5-08 | data drift와 개선 반복 |
| S5-09 | 백업과 복구 및 보존기한 |
| S5-10 | 서비스 보안과 운영 설정 |
| S6-01 | 오픈소스와 모델 라이선스 정책 |
| S6-07 | 외부 기여와 프로젝트 운영 |
| S6-08 | SDK와 자동화 API |
| S6-09 | 공개 위생과 지원 자료 |
| S6-10 | 브라우저 및 확장 개발 경계 |
| S7-04 | 팀 동시 작업과 fault injection |
| S0-09 | 초기 UI와 Electron 검증 환경 |
| S6-11 | 공개 배포 라이선스 결정 |

## 2026-10-08 영속 실행·migration·장애 matrix 추가 검증

- `1a9b830`: 영속 controller의 실제 main/backend 소유권 handshake와 durable inspect, renderer 재열기/응답 유실 거절을 연결했다. renderer/main952개와 실제 browser3/Electron1이 통과했다. 첫 backend388개에는 stale fixture 실패3개가 있었고,385개 결과를 보존한 뒤 수정한 command/inventory/resource 범위36개를 다시 검증했다. 두 번째 전체 suite 통과로 표기하지 않는다.
- S1-08: 원래4개 구현 조건과24개 소스 이력을 독립 검토했고 현재 원래 회귀26개가 통과했다. 현재 지원 POSIX 프로토콜의 구현만 verified이며 legacy/unknown worker는 drain/refusal 또는 추가 adapter가 필요하다. 실제 사용자 home·installed native 이관·대표 품질·다른8개 gate는 pending이다.
- S7-04 `213e9b7`: 독립 원본/수정 검토, 최신 실제 matrix61개와 focused44개가 통과했다.35개 필수 selector의 모든 parameter case를 보존한다. 부모 pytest filter가 필수 parameter를 빠뜨리는 문제를 재현한 뒤 child 환경에서 제거했다. CPU MemoryError는 주입한 장애이며 실제 CLI의 failed publication·살아 있는 reservation·owned exit 후 해제와 별도 process/예약 보존을 검증한다. 물리 장애·실 publisher native update·다른8개 gate는 pending이다.
- S7-01 현재 curated action207개·verified569개·pending880개. 두 개 신규 launch action의5차원은 실제 UI에서 검증했고9차원은 pending이다. GUI lifecycle은 CLI/서명 응답 fixture를 사용하며 실제 packaged native startup/검사 추론 근거로 바꾸지 않는다.

## 2026-10-08 추가 구현·실제 앱 검증

| 묶음 | 이번에 확인한 완료 범위 | 다음 작업 |
|---|---|---|
| 기존 설치 데이터 인수 | bounded preview·명시적 운영자 소유/중단 확인·원본 backup·owner 원자 게시·기존 이력 유지 | 실제 설치 target·지원하지 않는 live worker·독립 수락 |
| 업데이트 실행 소유권 | 예약/실행/불명확 상태 영속 기록, update·migration 충돌 거절, 정확한 receipt 재시도 | 실행 중 backend admission 전이 수정, native controller·handshake·process-tree·기준 이미지 실행 |
| 선택 이미지 오류 | 미검증/손상/내용 hash 없는 이미지 거절, 검증된 이전 선택 보존 | 남은 취소·통신 장애·하위 실행 시나리오 |
| 평가 화면 | 저장된 controlled report의 클래스 지표·FN/FP·확률/면적 분포·ROC 수학·재열기 | 실제 모델 대표 truth와 사람이 판정한 품질 수락 |
| 대규모 metadata GUI |10만 행 실제 paging/search·검증된 tail 선택 복원·없는 파일의404/선택 거절 | 실제 사진 규모·target RAM/p95/택트와 동일72시간 완주 |
| 수동 candidate 검증 | 기존 보호 환경·main·publisher 형식·고정 source/bytes·license·서명 검증 gate 코드 | 실제 publisher·signed artifact·installer·hosted 실행 |

정확한 소스369b8e1에서 통합 backend412개, renderer/main922개, 집계/범위39개, candidate 검증29개가 통과했다. Clean browser3·native4 실제 GUI case는 모두 retry0으로 통과했으며 소유한 test process가 종료된 것까지 확인했다. 공개 receipt는 `2026-10-08-remaining-software-closure-369b8e1.json`이다. 합성 저장 보고서를 실제 모델 추론으로,10만 metadata를10만 사진으로, launch 기초를 실제 updated-app 실행 완료로 집계하지 않는다.

## 이전 실행 근거와 후속 기록

아래 각 기록의 숫자와 제약은 그때의 소스에 대한 이력이다. 최신 집계와 구분해 읽는다.

- 실제 SAM2/텍스트·이미지 예시·few-label 업데이트, AutoDL 측정/재사용/재학습/중단 및 원격 exact resume를 확인했다.
- 실제로 학습한DINO 모델5개, 동일 untouched test3장, native 비교/재열기/JSON export, 전체 flow 평가와 별도 package 프로세스 parity가 통과했다. 합성 fixture이므로 공정 품질은 승인하지 않았다.
- 종료된 로컬 history 변환·원본 보존·CAS·cutover 후 receipt·경로/권한 거부와 완료 게시 순서72개 통과. live worker/lease 이관은 미완료다.
- process/package/source 계약171개 통과, native Windows 전용2개 skip. Windows hosted unsigned package 실제CPU train/evaluate/infer/export·두 번 실행·재시작 동일 기록·소스/실행 파일 해시 대조 통과.
- 비교/whole-flow/package 계약92개 통과. 최신 소스 공개 CI는 별도 gate이며 이전 실패도 보존한다.
- 72시간 연속 운전·사람 품질 승인·실장비·운영 계정·서명·지원 책임은 독립 조건으로 남긴다.

출처: registry와 source freeze·명령·JUnit·GUI trace·SHA receipt. 각 검증의 source/platform/synthetic 범위를 유지한다.

## Manual review and release-gate follow-up

See `2026-10-06-manual-review-release-gates.md` for the saved candidate/comparison
review bridge, first-package UI reachability, account invalidation, actual
automated warm-start receipt correction and release-readiness distinction.
33 backend,835 renderer/main,9 readiness/recovery controls and one actual
DINOv3 development Electron retrain/reopen gate pass. Parent accounting stays
64 software verified/18 pending at that earlier recorded slice.
Exact2c907b1 hosted source CI failed on stale generated API types; regeneration
and its7-case regression pass, with full hosted rerun still required.

## Runtime history, converted review and central deployment follow-up

See `2026-10-06-runtime-history-converted-review.md` and its paired receipt.
Current ended runtime indexes, explicit converted whole-graph review, poisoned
unlisted bytecode refusal and mandatory new central graph authorization are
implemented. Actual macOS Electron performs two-agent CPU deployment, canary,
disconnect continuity, reopen and rollback with separate graph selections.
The 24-case Chrome run predates the central gate changes and remains scoped to
its recorded freeze. The separately recorded actual converted native review/library/reopen/service
IR gate promotes S4-14 software implementation, giving65 verified/17 pending.
No final acceptance count is promoted. The first 72-hour attempt has an observed interrupted exit; the second stopped
at5 cycles and could not write a terminal child receipt after disk exhaustion.
Neither is a completed endurance qualification and their durations are not added.

## Complete improvement loop and external temporary storage

See `2026-10-07-improvement-external-history.md` and its paired receipt.
One fresh actual CPU/native run covers reviewed new input, authentic DINOv3
parent/candidate training,24 fixed heldout comparisons, explicit fixture model
and whole-graph review, independent inactive candidate graph, reopen,24-input
package parity and managed HTTP inference. Current graph/service observation is
shown after native reload without changing the original active graph or model.
This promotes the manual S5-08 software path to66 verified/16 pending.
Synthetic authority does not approve manufacturing quality; automatic promotion
is not recorded as supported. Independent/final acceptance remains0.

The exact a184b1e hosted Linux source run completed successfully. The new delta
still needs its own hosted source run. New heavy fixtures, CI downloads and the
fresh72-hour run use an owned APFS sparse image on the external `backup` volume.
Existing external backup files remain unchanged. Keep the external drive mounted
while the endurance run is active; no previous elapsed time is added.

## Interrupted restore follow-up

See `2026-10-07-interrupted-restore-recovery.md` and its paired receipt.
The exact recovery generation/hash/fence is durable before pointer publication.
Actual abrupt process exit and fresh CLI retry, original preservation, copied
session revocation and refusal to overwrite later writes pass116 affected tests.
CI selection controls pass18 tests. This closes a component of S1-08/S6-04,
without promoting either parent or installer/native/quality acceptance. Source
The workflow requests preservation of pending as well as running qualifications.
Later readback observed19dfb64 cancelled without a job; the cause is unconfirmed,
and it is not a hosted success. The independent external72-hour run keeps its
original source and duration.

## Parallel priority and CI follow-up

See `2026-10-07-parallel-priority-ci-recovery.md` and its paired receipt.
The specialist failure journal waits for its owned fenced reservation return.
The template GUI scenario confirms the partial draft save before replacement;
the team scenario waits through an initially unassigned async response. Related
backend88, mapping guard25, browser2 and actual macOS Electron2 cases pass.
The GUI runs retain dirty source identities and are not strict clean-source
acceptance receipts. No parent accounting changes. The72-hour qualification is
independent and its complete-duration readback is last.

Fresh clean1352bff browser/Electron reruns also pass4/4 without retries.
The strict collector checks the exact historical spec bytes, single expected
passed report result, clean harness identity and screenshots for each run.
Four `2026-10-07-clean-template-draft-*` / `clean-team-reconnect-*` receipts
are retained. They qualify those exact executed cases, not all156 actions or
independent/human/physical/release acceptance.

## DICOM pack and action/candidate follow-up

실제 배포 DICOM pack, 원본을 보존하는 CPU/provider·clean browser/Electron 실행, 해독 전 resource/frame 거절과 구체적인 입력 오류 안내를 완료했습니다. 관련 backend52개, 기능 근거144개, renderer849개와 타입 검사가 통과했습니다. 버튼19개의32개 시나리오를 실제 기록에 연결했고, 공개 후보는 미해결 조건을 유지한 hold입니다. 부모 집계66/16/0은 유지하며, 72시간 검사는 독립적으로 계속 실행합니다.

상세 기록: [DICOM runtime and action/candidate inputs](2026-10-07-dicom-actions-candidate.md).

## 실제 앱 복구·진단과 DICOM 무결성 후속 작업

`2026-10-07-app-recovery-dicom-integrity.md`에 원본 해시 보존·새 프로젝트 복원·선택형 비식별 진단·검색/재열기의 clean browser/Electron6건과12개 스크린샷을 연결했다. DICOM cache/receipt/source 재검증·중단 후 atomic publication·자동 window identity를 수정하고 관련171개를 통과했다. 후속 dirty GUI 진단은 clean 실행 근거로 세지 않고 별도 재실행한다. 부모 집계66/16와 최종 수용0은 유지한다.

## clean 앱 콘텐츠·파생 버전과 입력 경로 후속

깨끗한5cfe9bc에서 browser7/Electron7, 총14건과40개 스크린샷이 통과했다. 라벨 좌표·독립 방향·두 클래스 mask·3가지 format·그룹 분할·파생 검수/새 데이터/CPU 재학습/16개 고정 시험 비교·원본 복귀와 RLE frame을 실제 실행했다. 학습 fixture는 API 제출이며 GUI 학습 버튼 전체를 검증한 것으로 세지 않는다. folder picker는 controlled native response이고 human picker/cancel은 미검증이다.

허용된 dataset alias의 DICOM prepare200/GET422 오류를 재현하고 경로 보존으로 수정했다. 대상이 바뀐 alias의 기존 cache는422로 거부하며 원본/cache 바이트를 보존했다. 관련64개가 통과했다. action 원장은55/106/279, 기능별 native 근거52곳이다. 근거 검사133개도 통과했다. parent66/16 및 최종 수용0을 유지한다. 1352bff hosted CI 완료와 현재 delta의 CI는 구분한다. 72시간은 기존 고정 실행의423cycle/약7시간 시점에도 running이며 대기하지 않고 후속 구현을 진행한다.

## 외부 mask·이름 있는 라벨 세트·근거 복귀 후속

추가 clean964e032 browser5/Electron3, 총8건/38개 스크린샷이 통과했다. 외부 multiclass mask의 palette/빈 클래스/구멍·preview 변경/merge 충돌 거부·backup·실제 download/reimport, 이름 있는 labelset와 개별 flags/tag color, class 통계에서 gallery filter, completed comparison 작업의 재열기·현재 검수 이미지/원래 비교로 복귀·현재 라벨 편집과 역사적 report 불변을 실행했다. 저장 mask8개는 원본 reference와 모든 픽셀/class ID가 일치했다.

원장은18개 feature의71action/144verified/353pending이며 최종 feature 수용은0이다. 새 action의 미실행 시나리오도 전부 기록하여 pending 시나리오 수가 늘었다.1352bff hosted log는 CPU2283pass/7skip, browser162pass/1skip을 보여 준다. 최신 소스CI 완료나 authentic weights/skip 조건 전체 검토로 세지 않는다.


## 실행 중 백엔드의 업데이트 잠금과 workflow 문법 수정

소스 `e3c4d7c40c74ea827bdd2b8782d69c7658b22de3`에서 실제 별도 프로세스의 shared admission 때문에 lease claim/ready가 거부되는 문제를 재현했다. 설치 shared admission과 nonce별 mutex, 기록의 정확한 CAS로 수정했다. bool/float로 손상된 상태를 정상으로 받아들이던7건도 거절하며, 관련228건이 실패·skip 없이 통과했다. 실제 spawn 뒤의 불확실 종료는 여전히 recovery_required로 남는다.

GitHub run37642522031은 release workflow 파일 검증 단계에서 실패했고 실행 job은0개였다. 두 runner.temp 경로를 job env에서 step env로 옮겨 실제 actionlint 문법·context 검사와 관련30개 회귀가 통과했다. 수동 protected main 후보 검사이며 실제 실행·서명·배포는 하지 않았다. 새 소스 hosted CI 완료는 아직 확인하지 않았다.

원래 부모67verified/15pending, 요청 범위13pending은 유지한다. persistent native controller·authenticated handshake·process-tree reconciliation·known-image execution·OS installer adapter를 다음 구현으로 계속한다. 이전7개 실제 GUI는 소스369b8e1의 근거이며 최신 소스의 전체 GUI 완료로 바꾸지 않는다. 근거: `../verification/receipts/2026-10-08-launch-transition-workflow-e3c4d7c4.json`.

## 추가 실제 이미지 선택 화면 검증 · fe92877

브라우저·네이티브 Electron에서 각각 retry0으로 통과했다. 제어된503 오류 후 재시도, 취소 뒤 늦은409 응답, UUID/SHA/path의 테스트 미리보기 전달3차원을 추가 검증했다. 현재 curated action207개·verified572개·pending877개다.10만 metadata와 실제 이미지3장만 사용했으며 모델 추론·10만 사진·72시간·전체 기능 수락은 완료로 바꾸지 않는다. 이전 setup timeout과 native 검사 클라이언트의 인증 실패는 실패로 보존하고, 실제 renderer session으로 수정해 두 mode를 모두 다시 실행했다. 근거: `../verification/receipts/2026-10-08-metadata-picker-closure-fe92877.json`.

## 공개 CI 실패 원인 수정과 로컬 재검증 · 52d3c3a

이전 공개 CI0c84e66의2건 및 c15df8d의6건 실패를 보존했다. retry 시험의 전역 mock 간섭, preparation 정리 중 lease 유지에 대한 오래된 시험 기대, child session 공통 규약, runtime pack API 자동 생성 타입 누락을 수정했다. 실제 현재 소스의 관련152건과 타입 생성7건, renderer/main 타입 검사가 통과했다. Windows native2건 skip은 통과가 아니다. 관련154-outcome 실행 소스4b66d9b 뒤의 유일한 코드는 자동 생성 타입7줄이며 backend/test hash는 동일하다. 전체 hosted CI 최신 HEAD 성공이나 release 완료로 바꾸지 않는다. 근거: `../verification/receipts/2026-10-08-current-source-ci-remediation-52d3c3a.json`.

## 패키지 worker 시작 격리와 실제 C# 실행 · 94118fb

실제 CPU 실행에서 package의 미등록 sitecustomize와 부모의 비밀·plugin 환경 상속을 재현해 수정했다. 소스 worker는 -I/-B·전용 bytecode prefix·전용 임시 작업 폴더·사용자 폴더·캐시·제한된 환경을 쓰며 기존 offline 설정을 유지한다. 현재 소스에서17건 통과·2건 환경 gate였고, 공식 hash를 확인한.NET8 SDK를 외장 임시 공간에 준비한 뒤 C# compile/PInvoke CPU 전체 graph·reference parity·deadline 종료1건도 통과했다. 중복 없는 통과18건이며 Linux loader는 이 macOS에서 실행했다고 바꾸지 않는다. 기존72시간 worker·GPU·권한·release 상태는 변경하지 않았다. 원래 부모69/13·요청 필요11·종합 수락0은 유지한다. 근거: `../verification/receipts/2026-10-08-owned-package-startup-94118fb.json`.

## 저장 평가 새로고침의 빈 목록·오류·다시 열기 · 530803a

실제 브라우저와 macOS Electron에서 각1회, retry0으로 통과했다. 빈 프로젝트의 새로고침200, 제어된503 뒤 기존 이력·결과 제거와 재평가 차단, 명시적 재시도 뒤 정확한 ID/SHA 복원, 실제 reload 후 선택 유지3차원을 추가 확인했다. 저장된 시험 보고서·입력 hash와2105개 소스는 불변이며 평가·학습·job 생성은 없었다. 현재207개 행동의575개 시나리오 검증·874개 pending이다. 부모69/13·요청 필요11·종합 수락0, 모델 추론·사람 품질 검수·72시간은 그대로 남는다. 근거: `../verification/receipts/2026-10-08-saved-evaluation-refresh-closure-530803a.json`.

## 원래 백엔드 epoch에 고정한 실제 CPU 검증 · 3f3ac66

등록된 프로젝트·계획·원본 앱과 데이터 세대를 독립적으로 고정하고, 확인된 원래 controller/main/backend 연결로만1회 CPU 검증을 전달한다. 현재 조합 소스의102건 Python 검사와15건 main bridge 검사가 통과했고 main·preload 타입 검사도 통과했다. 생성한 시험 OCR 체크포인트가 실제 별도 CPU 계산으로 문자 A를 반환했으며 결과 hash와 CUDA 비가시 환경을 확인했다. 시험용 main/backend·체크포인트 근거이며 학습 완료 출처나 설치 앱·frozen·모델 품질 수락이 아니다. 사전 실행 S010·전체 writer/프로세스 계보·설치 adapter는 이어서 구현한다. 부모69/13·요청 필요11·종합 수락0, 행동575/874는 유지한다. 근거: `../verification/receipts/2026-10-08-owned-source-cpu-epoch-3f3ac66.json`.

## 쓰기 잠금 실행 소유자 보정 · c7d852d

배경 스레드·비동기 작업·fork가 복사한 context로 부모 잠금을 빌리던 결함을 수정했다. 실제 서로 다른 스레드에서 같은 숫자 ID가 재사용되는 경우도 재현했고, PID·Thread 객체·비동기 Task의 살아 있는 소유권으로 재진입을 제한했다. 현재 조합의62건은 skip 없이 통과했다. 신호 종료 fallback이 있는 기존4건은 명시적으로 제외했으며 새 실제 fork 검사는 협력 종료·회수를 확인했다. 전체 writer 목록·프로세스 계보·staging 권한·native 설치는 별도 미완료다. 부모69/13·요청 필요11·종합 수락0 상태를 유지한다. 근거: `../verification/receipts/2026-10-08-cooperative-writer-owner-c7d852d.json`.

## 임시 데이터 영역과 준비 상태의 권한 수명 · 446588e

임시 데이터 영역은 원래의 살아 있는 독점 잠금과 유효한 작업 범위가 함께 있어야 쓸 수 있게 수정했다. 종료한 범위를 새 잠금으로 되살리거나 다른 스레드·비동기 작업이 준비 상태를 대신 승인할 수 없다. 현재 소스 조합의84건 검사가 통과했고 실제 시험 SQLite·프로필 쓰기 보존을 확인했다. 네이티브 인증·전체 writer와 프로세스 계보·설치 adapter 수락은 별도이며 부모69/13·필수11·종합 수락0과 행동575/874는 유지한다. 근거: `../verification/receipts/2026-10-08-privileged-reader-ownership-446588e.json`.

## 활성화 전 기준 이미지 실행·화면 복구와 CI 검토 경합 · fbcdc7a

서명된 source candidate는 검토한 프로젝트3개 pin에 묶인 실제 CPU 기준 이미지 결과가 있어야 DB와 앱 pointer를 바꿀 수 있다. 중앙 게시와 직접 복구에도 같은 검사·봉인 기록을 적용했다. 현재 조합의 backend343건, 화면961건과3개 타입 검사, 실제 browser2/Electron1이 통과했다. 브라우저에서 적용·새 계정과 라벨 보존·중단 복구·재열기를 확인했고 Electron은 미설정 개발 설치를 거절했다. 생성한 OCR checkpoint와 시험 서명이며 실 publisher·compiled candidate·전체 계보·품질 수락은 미검증이다. 근거: `../verification/receipts/2026-10-08-staged-source-canary-fbcdc7a.json`.

이전 hosted CI의2797 통과·1 실패·17 skip을 보존했다. 실패는 실제 학습 reservation heartbeat 뒤에 오래된 검토를 적용한 경우였다. 보호는 유지하고 테스트가 기존12초 checkpoint 안에서 새로 검토하도록 수정했다. 현재31건의 초기 통과와 runner 기록 폴더를 바로잡은2건의 별도 재실행으로33개 고유 case가 확인됐다. 새 전체 hosted 결과는 별도로 읽는다. 부모69/13·필수11·종합 수락0과 행동207개·575/874는 유지한다. 근거: `../verification/receipts/2026-10-08-live-ocr-ci-race-fbcdc7a.json`.

## 원래 backend epoch의 compiled CPU 실행 · 4a264cc

고정 compiled worker를 별도 entry로 연결했고 실제 빌드에서 A/OK 기준 이미지 계산이33.119초에 끝났다. 같은 backend epoch의 봉인 기록을 반복 조회하고 결과 변조·부분 기록을 거절했다. 현재 코드 조합은 초기105 통과와 조기 inventory 거절 기대를 맞춘1개 재실행으로106개 고유 case를 검증했고 main11건·타입 검사도 통과했다. 실제 binary는 기록한 이전 isolated slice의 빌드이며 현재 전체 source의 staged candidate나 packaged Electron 성공으로 세지 않는다. compiled candidate 설치 adapter와 전체 계보·실 publisher·대표 품질 조건을 계속 진행한다. 근거: `../verification/receipts/2026-10-08-owned-compiled-cpu-4a264cc.json`.

## 설치 화면의 명시적 canary pin 연결과 근거 갱신 · 75af77c

기존 native layout 화면 시험은 새 기준 이미지 pin 입력을 포함하도록 갱신했다. 실제 clean-source 브라우저에서 한 번 실행해 통과했고 채널 불일치, 취소·재열기, 확인하지 않은 설치 차단, 서명된 내부 링크 변조 거절과 원래 라벨 보존을 다시 확인했다. canary 게시 자체는 이 사례의 통제된 fixture이며 실제 수학 실행이나 native 설치 수락으로 계산하지 않는다. 새 spec을 먼저 게시한 hosted run37681211730은 이전 GUI 근거의 spec 해시 불일치8건으로 중단됐다. 실제 새 실행 근거로8개 참조를 갱신했고 기존 실패 기록은 보존했다. 부모69/13·필수11·종합 수락0 및 행동207개·575/874는 유지한다. 근거: `../verification/receipts/2026-10-08-portable-native-layout-75af77c-browser.json`.

## 남아 있는 실행의 소유권 보존과 compiled 검토 응답 · 3fcf851

부모가0으로 끝나도 관측한 자식이 실행 중이면 결과 게시를 거절하고 같은 취소 handle을 보존한다. 최초 identity·관측·정리·진단 읽기 실패도 확인 대기로 남긴다. 기록한 원래 자식의 종료만 확인된 경우에 한해 같은 handle을 다시 사용할 수 있다. 현재 source의 deadline/Flow/GAN/SDK/CLI/C++ 소비자22건과 실제 survivor 기록5개를 확인했다. 이 검증은 전체·탈출 계보 또는 durable lease 정리 수락이 아니다.

compiled canary의 닫힌 protocol2/worker8개 필드를 화면 bridge에서 검토하며 실제 설치에는 원래3개 pin과 CAS 해시만 전달한다. 현재 main49건과 타입3개가 통과했고 별도 private Node977건도 통과했다. 새 CI는 source20건과 tree13건을 명시적으로 선택한다. 별도 private CI51건·현재 CI 계약18건이 통과했으며 실제 compiled fixture를 선택한 것으로 세지 않는다. 실제 staged compiled adapter와 provisioned native 성공 경로는 계속 구현·검증한다. 부모69/13·필수11·수락0과 행동207개·575/874는 유지한다. 근거: `../verification/receipts/2026-10-08-observed-group-and-compiled-review-3fcf851.json`.


## 2026-10-08 실행 불확실 상태의 private 파일 보존

- 소스 `ed0d25f`: Flow·GAN·OpenVINO에서 실제 관찰한 자식이 남아 있는데도 요청·캐시·후보 파일을 지우던 세 경로를 재현하고 수정했다. 독립 리뷰가 발견한 두 경로 교체 경쟁도 실패로 재현한 뒤 원래 폴더 FD에만 쓰기·정리를 묶었다.
- 불확실한 실행은 파일을 보존한다. 확인된 결과 뒤에는 원래 FD 안의 항목만 정리하고 빈 루트는 남긴다. 경로를 다시 해석해 루트를 삭제하지 않는다. 복구 marker는 실행 시도 intent이며 실제 spawn 수락 근거로 세지 않는다.
- 최종 private 관련 검사32개 통과·선택 OpenVINO 의존성1개 skip를 보존했다. 기존 별도 OpenVINO 인터프리터의 실제 변환·full-flow1개는 따로 통과했다. Root 통합 retention/서비스 CI29개 및 별도 source-lane18개가 통과했다. 각 실행의 정확한 소스·로그·XML·독립 리뷰 hash를 공개 receipt에 기록했다.
- 기존69/13 부모 집계와575/874 action 집계는 그대로다. 전체 process-tree, native Windows, 실제 publisher/target, 대표 품질 및72시간 수락을 추가하지 않는다.


## 2026-10-08 Studio 종료 뒤 독립 서비스 응답

- 게시된 clean `9c1e717`에서 browser1·source Electron1이 retry 없이 통과했고2130개 소스 파일이 전후 동일했다. 실제 서비스 시작·중지·재시작, 없는 패키지409 거절, controlled503 표시, 화면 취소·빈 프로젝트 이동·원래 프로젝트 재열기와 같은 release/epoch 보존을 확인했다.
- 원래 source Electron handle을 닫아 Studio/backend 포트가 종료된 뒤에도 독립 서비스는 같은 PID/birth/command/manifest로 ready 응답했다. 마지막에는 해당 owned 서비스만 중지하고 port closure와 harness의 leftover0을 기록했다.
- 두 이미지 synthetic fixture 승인·생성된 Torch CPU 모델은 시험 준비다. 이 두 UI case 안에서는 실제 새 모델 검사나 OpenVINO IR 적용을 하지 않았으므로 F099 IR의5개 pending 시나리오를 올리지 않는다. 실제 설치·전용 계정·재부팅·장비·publisher·대표 품질 및 부모 수락도 추가하지 않는다.
- 실제 실행 두 건과 통합 receipt를 별도로 고정했다. 기존69/13 부모와575/874 action 집계는 유지한다.


## 2026-10-08 앱 실행 private home 격리

- 기존 owned lease의 `PATH/LANG/VISION_*` 환경이 private HOME/cache/tmp 정책을 누락해 실제 계정 영역을 참조할 수 있는 네 경우를 no-spawn 경계에서 실패로 재현했다. macOS Foundation이 HOME만 변경하면 원래 계정 home을 반환하는 실제 metadata probe 실패도 보존했다.
- 원래 install 폴더 FD 아래 새 nonce home을0700으로 만들고 HOME·cache·tmp·offline·CPU-mask와 macOS `CFFIXED_USER_HOME`을 고정한다. 호출자의 secret/plugin/startup env는 복사하지 않는다. 실패·불확실한 종료에서 홈을 지우거나 재사용하지 않는다.
- Root 관련43개가 skip 없이 통과했고2131개 소스 파일이 동일했다. 별도 private 최종12개·독립 read-only 리뷰를 고정했다. CI에는 두 no-spawn 함수의4개 parameter-expanded case만 추가하고 actual native/compiled case는 선택하지 않았다.
- 실제 Electron/frozen 시작·일반 backend CPU 모드·동일 UID 공격자의 지속 path containment·전체 tree 종료·publisher·설치 대상·품질 및 부모 수락은 이 근거에 포함하지 않는다. retained home을 소비하는 향후 migration/clear에도 별도 소유권 처리가 필요하다.


## 2026-10-08 작업 목록3개 시나리오와 Studio 종료 뒤 실제 검사

- clean `3e921c7`의 browser1·source Electron2가 retry 없이 통과했고2137개 소스 파일이 동일했다. F024 작업 목록 담당 필터의 빈 결과·controlled 오류·명시적 화면 취소3개만 검증으로 올렸다. 원래 데이터·이미지·revision은 유지되고 늦은 옛 읽기가 새 빈 목록을 다시 그리지 않았다. native 옛 HTTP200 provenance는 주장하지 않는다.
- 원래 source Studio/backend를 닫은 뒤 독립 daemon의 같은 PID/birth/command/manifest에서 실제 CPU 이미지 검사1개가 completed였다. 같은 입력의 반복 제출은 같은 작업 ID·결과1개·completed event1개로 읽혔다. 저장된 원래 admission bytes/hash와 decoded HTTP 결과가 일치하고 package/policy/images가 보존됐다.
- 이 실행의 generated weights·synthetic approval/truth는 품질 수락이 아니다. 첫 검사 test의 decoded API 이름 오류 실패와 실제 완료 row/정상 owned cleanup도 이전 근거로 보존했다. Root는 수정된 case를 clean 소스에서 다시 실행했다.
- action 집계는578verified/871pending, 구현 부모는69verified/13pending 그대로다. 별도 공정 품질·publisher/설치·전용 계정/재부팅·물리 장치·전체 tree·72시간 완료는 올리지 않는다.


## 2026-10-08 팀 배정3개·실제 IR 서비스5개 시나리오

- clean `3be69bb`에서 browser1·source Electron2가150.50초, retry 없이 통과하고2143개 소스가 동일했다. F024 배정의 빈 작업자·실제 우선순위422·정확한 controlled POST503 후 명시적 실제 재시도3개를 검증했다. 선택 image UUID/revision·라벨·mask 보존과 성공 재시도1회만의 배정 revision 증가를 확인했다.
- 실제 CPU→OpenVINO 변환·별도 정밀도/전체 흐름 검토·IR 서비스 적용·입력 completed를 수행했다. 빈 경로·없는 경로409·정확한 적용 POST503·명시적 화면 닫기/프로젝트 전환·원래 IR epoch 재열기5개를 검증했다. GET 상태503은 적용 오류의 근거로 대신하지 않았다.
- 원래 native 지연 HTTP200 provenance는 거짓이다. 별도 인증된 실제 GET의 snapshot을 원래 UI 응답에 controlled200으로 전달했고, 요청 종료와 새 빈 프로젝트에 옛 상태가 다시 그려지지 않는 범위만 확인했다.
- generated allOK weights가 syntheticNG를 놓친 결과는 보존했다. controlled 검토/정답은 사람의 모델 품질 승인이나 설치·publisher·장비·whole tree 수락이 아니다. 실제 owned daemon/app/backend/port 정리는 확인됐다.
- action 집계586verified/863pending, 구현 부모69verified/13pending, Windows 면제 후 요청 범위11pending을 유지한다. 공개 CI의 `0b7501a` CPU 회귀 통과·browser191통과/1실패도 유지하며 최신 소스의 hosted 성공으로 대신하지 않는다.


## 2026-10-08 기준서5개·미전송 배정 닫기1개와 파생 workflow 재검증

- clean `734a766`에서 browser2/source Electron2가119.44초, retry 없이 통과하고2148개 입력이 동일했다. 빈 작업자·중복 클래스 로컬 거절, 정확한 books POST503 뒤 명시적 실제200 재시도, 미전송 기준서 닫기, 발행v2의 실제 라벨 palette 전달5개를 검증했다. 미전송 배정 닫기1개는 server record 보존/재열기 시 초안 유지이며, 전송 중 요청 취소나 rollback이 아니다.
- 실제 새 category 선택 상태를 접근성에도 표시하도록 기존 Boolean의 aria-pressed 한 줄을 추가했다. 원래 라벨·mask·검수 기록·이미지 hash는 보존하고 annotation 저장은 하지 않았다.
- 실제 hosted `0b7501a` 실패 trace에서 comparison POST가 없음을 확인했다. 후보 화면 hydration 중 catalog refresh가 클릭 시 버튼을 잠시 비활성화했다. 정확한 후보 job 표시·모델 쌍·전체 test/로컬CPU·활성 버튼을 먼저 기다리고 기존10초 응답/95초 학습/65초 비교 제한을 유지했다. 실제 원본/CPU warm-start 학습, 동일16개 고정 test 비교, 원본 복귀를 root clean source에서 다시 수행하고 기존25개 근거 참조만 새 실행 기록으로 갱신했다.
- 집계592verified/857pending, 부모 구현69verified/13pending을 유지한다. 새 hosted CI·사람 품질·설치·publisher·장비·전체 부모 수락으로 표시하지 않는다.


## 2026-10-08 staged compiled worker와 Electron resource 경로 보정

- 고정 compiled worker의 인증·단회 anonymous transport·입력 및 runtime/resource hash를 활성화 pointer 변경 전에 확인하고, 불확실한 실행은 원래 intent와 ownership을 남긴다. source229개 및 CI selector 계약20개가 통과했다. 명시적 source selector21개를 공개 CPU CI에 추가했다.
- 이전 exact compiled artifact에서는 실제 A/OK 기준 이미지1개와 거절4개가 통과했다. 최초 pointer가 비어 있던 합성 검증이며, 기존 설치 앱의 업그레이드·전체 process-tree 종료·품질 수락이 아니다.
- 실제 Electron Helper(GPU/Plugin/Renderer)의 정상 괄호 resource 경로9개가 거절된 원인을 확인해 경로 parser만 좁게 보정했다. traversal·reserved name·namespace·link/hash 검사는 유지했다. 보정된 compiled artifact와 실제 native positive 실행은 후속 receipt로 구분한다.


## 2026-10-08 팀 목록 재열기·정확한 이미지 전달2개

- clean `7d2e478` browser1/source Electron1이40.77초, retry 없이 통과하고2157개 입력이 동일했다. 같은 프로젝트에서 dialog를 명시적으로 닫고 다시 열어 유지된 담당·상태 필터로 실제 queue GET200을 갱신했다. 기존 이미지와 다른 row를 클릭한 뒤 실제 annotation GET200과 화면의 UUID·경로·내용 hash가 정확히 일치했다.
- 원본·라벨·검수 기록 hash를 보존하고 UI mutation은 없었다. 같은 context의 재열기이며 앱 재시작 후 필터 영속화나 사람 정답 승인이 아니다. invalid filter는 계속 pending이다. 집계594verified/855pending, 원래82/156ID와 부모 구현69/13 및 전체 수락0을 유지한다.


## 2026-10-08 라벨 가져오기 빈 입력·오류·충돌6개 검증

- clean `f8332dd`에서 browser1/source Electron1가38.84초, retry 없이 통과하고2161개 입력이 동일했다. 폴더·미리보기 부재 시 버튼 비활성/no POST, 실제 경로 traversal422, 동시 metadata 수정 뒤 실제 stale revision409, 정확한 preview/apply POST503 이전의 dispatch 차단과 명시적 실제200 미리보기 재시도를 확인했다.
- 합성 LabelMe 제안은 적용하지 않았다. fixture의 lot/revision 수정1개는 라벨·검수 변경과 분리해 정확한 workflow ledger1개만 변함을 확인하고 이후 전체 annotation bytes·원본 hash·버전 기록을 보존했다. 폴더 대화상자는 통제된 fixture 응답이며 사람의 OS-picker 사용 근거가 아니다.
- 집계600verified/849pending, 부모 구현69verified/13pending이다. 설치·publisher·사람 품질·새 hosted CI·전체 부모 수락은 pending을 유지한다.


## 2026-10-08 동일 compiled4 worker 실행 검증

- 실제 compiled4 바이너리에서 A/OK·ROI1개·결함0개를 두 pointer 변경 전에 봉인하고 원래 installer 종료 이후 같은 receipt를 읽었다. 다른 parent의 capsule 재실행은 private bytes를 바꾸지 않고 거절했다. 1ms deadline은 기존 pair와 복구 기록을 보존하며 finish/abort 재실행을 거절했다.
- 최초 cold missing-FD 호출은30초 제한을 넘겼다. 동일 소스·fixture·30초로 수행한 재시도가 통과했다. 최초3pass/1fail과 이후2pass를 각각 보존하고5개 고유 케이스 성공 관찰로 집계하며, 한 번의 전부 성공 실행이나 cold 시작 성능으로 표시하지 않는다.
- root가339개 소스와335개 exported resource,11729개 artifact file·95개 link를 독립적으로 다시 읽었다. null prior pair·inert main인 임시 설치 fixture이며 실제 Electron/native bootstrap·전체 tree·실사용 설치·publisher·사람 품질·전체 수락을 대신하지 않는다. 부모 구현69/13은 유지한다.


## 2026-10-08 실제 앱 시작 순서 실패 수정

- frozen native 실행의 descriptor EOF를 보존하고, 별도 private 진단에서 원래 backend binding이 완료되기 전에15초 HTTP health 검사가 시작해 종료된 것을 확인했다. 같은 bind promise를 먼저 기다리도록 순서만 바꿨다. port180초·bootstrap210초·health15초와 권한·인증·종료 조건은 유지했다.
- 실제 supervisor source를 transpile한 회귀2개는 수정 전 실패하고 수정 후 통과했다. root의 renderer/main typecheck와 기존 CI 계약20개도 통과했으며2165 tracked inputs는 전후 동일했다. CI에 이 회귀 명령을 추가했다. child는 통제된 handle이고 HTTP는 실제 loopback이며 실제 native 성공으로 세지 않는다.
- 새 frontend 묶음과 같은 coherent4 backend의 실제 frozen-controller 실행은 별도 검증 중이다. 설치·publisher·전체 프로세스 트리·최신 hosted CI·부모 전체 수락은 여전히 pending이다.


## 2026-10-08 팀 검수 거절11개와 노드 근거 제어5개

- clean `e28d1d9`에서 browser2/source Electron2가 retry 없이 통과했다. 빈 작업자/사유의 no POST, 실제 자기 검수·조정422, 유효 POST의 통제503, 미전송 조정 닫기와 실제 reload 후 원래2개 fixture 의견·분쟁·라벨·원본 JSON/hash 보존을 확인했다.2개 합성 의견 생성은 별도 준비 작업이며 사람 품질 승인과 구별했다.
- U006 검색·12/12/6 페이지·상세·활성 ROI 경로·키보드 확대/복귀는 실제 UI로 실행했다.30개 결과는 표시 fixture이며 모델 추론이 아니다. 저장 그래프/원본은 불변이며 reload가 오래된 실행을 되살리지 않는다. U006은 기존 행에 좁은 GUI 근거만 추가했고207개 curated action 목록은 늘리지 않았다.
- 집계611verified/838pending이다. 미실행 앱 동작과 기존 부모 전체·품질·설치·publisher 수락은 pending을 유지한다.


## 2026-10-08 실제 private 앱의 같은 backend CPU 추론 통과

- 원래 frozen controller→실제 Electron→frozen backend에서 A/OK/1ROI/0결함을 실행하고 같은 receipt를 compiled 재조회2회로 검증했다.448.524초1passed/0skip,2130개 소스 불변이며 frontend456/backend339와 export335개를 root가 대조했다. 관련 source admission/cleanup14개와 e2e 타입 검사도 통과했다.
- AppKit 정상 종료 후 원래 등록된 controller/main/backend의 종료를 확인했으며 lease는 recovery_required로 보존했다. 전체 process tree 종료나 설치판·publisher·OS 서비스·현장 모델 품질 승인으로 세지 않는다. 이전 primary8 실패와 불확실한 프로세스 기록도 유지한다.
- 실제 실행은 private d3 기반에 해시 고정된 선행 수정과 test-only fixture를 합성한 freeze이다. 새 root 소스 커밋의 전체 앱 빌드였다고 주장하지 않는다. backend 소스가 추가 변경되면 이 binary의 기존 lineage를 별도로 유지한다.


## 2026-10-08 Linux 제어 명령의 불필요한 모델 초기화 수정

- cold global_migration 시작마다 engine package가 PyTorch/device/dataset을 먼저 가져오는 원인을 실제 Linux에서 확인했다.35개 public export·순서·동일 객체·star import를 보존하는 lazy initializer로 수정했다. 원래 OCR의12초 checkpoint·30초 runtime·재검토3회·소유권·신호 조건은 변경하지 않았다.
- 수정 전 cold 회귀6실패→수정 후7통과, 관련75통과. GPU0인 기존 pinned Linux CPU 환경에서 원래 OCR natural/heartbeat2개가7.55초에 통과하고1514개 소스가 불변했다. actualCLI가0.4초대로 단축됐으며 heartbeat의 stale plan을 실제 거절한 뒤 fresh pair로 정상 적용했고 worker 재시작/예약 삭제는 없다. root 합성9개도14.50초 통과/2169소스 불변이다.
- 실제 native 앞선 성공은 원래 eager initializer를 포함한 coherent4 frozen binary의 근거다. 새 lazy backend를 native로 재검증했다고 이전 근거를 넘기지 않는다. GAN 종료 경계와 hosted 앱5개 실패는 별도 해결 중이다.


## 2026-10-08 미전송 검수와 로컬 초안 비교8개

- clean `9e019e1`의 browser/source Electron2건이 retry 없이 통과했고2178개 소스가 동일했다. 승인·반려 초안을 닫고 실제 reload/reopen해도 기존1개 fixture 의견과 raw label/workflow/source hash가 그대로이며 UI 쓰기 요청은0개였다.
- 실제 canvas로 만든 로컬 초안은 reload 후 바이트 단위로 보존된다. 빈 초안의 제어 부재, 다른 원본 SHA 적용 차단, exact GET의 통제503, 실제200 비교 후 기준·서버·로컬 라벨 readback을 확인했다. 사람 검수와 저장·적용은 수행하지 않았다.
- 기존207개 curated 행동의619개 시나리오 verified/830개 pending이다. 부모 구현69/13과 전체 수락0은 유지한다.


## 2026-10-08 원래 프로세스 종료 경계 판정 수정

- `9e019e1`의 runtime은 원래 birth/group/session·미회수 Popen과 반환 row session이 일치할 때만 잠깐 비어 있는 명령을 관찰 상태로 보존한다. strict signal/child enrollment 판정은 여전히 false이며 기존 UNKNOWN은 지우지 않는다.
- 원래 Linux GAN30초 gate가 실패했던 자연 경계에서 수정 후7.665초로 통과했다. 같은 empty command와 non-Z row가 실제 관찰됐고 원래 Popen 종료 후에만 completed0이었다. root40개와 private40개도 skip 없이 통과했다. 타임아웃·외부 row/session·PID 재사용·미등록 자식·관찰 실패 거절을 보존했다.
- 이미 배포된 frozen/native 근거는 옛 binary/source에 고정되어 있고 이번 소스에 옮겨 세지 않는다. 전체 process tree와 설치/publisher/대표 품질·최신 hosted CI 수락은 아직 별도다.


## 2026-10-08 Linux 앱 실행 실패5개 해결

- 원래5개 테스트를 clean `9e019e1`에서 그대로 실행해79.95초,5pass/0skip로 통과했다. 같은 실행의763개 입력 hash가 불변이며 CPU 격리 container가 종료됐다. 이전 `2e8667c`의5fail과 private setup 누락은 각각 보존한다.
- 앞선 실패5개 모두 실제 A/OK 결과는 만들었으나 original leader UNKNOWN 때문에 publication이 거절됐다. production 변경은 runtime 판정 하나이고 테스트/TS bridge/dependency/deadline은 같다. stale receipt·foreign claim·부분 publication·package shadow 거절은 성공으로 바꾸지 않았다.
- 현재 source 경로의 회귀 해결이며, 별도 최신 hosted CI와 새 compiled/native target 근거는 여전히 필요하다.


## 2026-10-08 라벨 교환·저장 평가 화면15개

- clean `f3895ec` browser/source Electron4건이 retry 없이 통과했고2185개 소스가 동일했다. 실제 원본 이미지 SHA·UUID·revision·annotation handoff와 평가 record/evidence SHA를 확인했다.
- 제안 라벨은 적용하지 않았고 물리 annotation tree·metadata·versions가 그대로였다. 저장 평가 fixture의9개 report와 입력도 동일했고 재평가·학습·job 쓰기는0개였다. 폴더 응답·선택 오류503·optional preference 손상은 통제 입력이며 취소는 완료된 미전송 화면 닫기/선택 popup Escape이다.
- 기존207개 curated 행동의634개 시나리오 verified/815개 pending이다. F059 group error와 부모 구현69/13, 전체 수락0은 유지한다.


## 2026-10-08 실제 IR 검토·라벨 내보내기15개

- clean `ed05005` browser1/source Electron2가 retry 없이 통과하고2192개 소스가 동일했다. 실제 conversion cancel journal과 별도 CPU→IR 두 입력 비교, 정확한 저장 package/runtime review 선택을 확인했다. generated allOK가 NG를 놓쳤고 사람의 품질 승인은 거짓이다. 서비스 적용 쓰기는0개다.
- LabelMe 빈 ZIP과 실제 YOLO ZIP5개 멤버·정규화 라벨·source/download SHA, exact controlled503 뒤 회복·재열기를 검증했다. 원본 이미지·라벨·metadata·versions는 동일하며 export cancel은 pending이다.
- 기존207개 curated 행동의649개 시나리오 verified/800개 pending이다. 부모 구현69/13, 전체 수락0과 Windows 면제2건은 유지한다.


## 2026-10-08 최신 clean 소스의 실제 private native 앱

- clean `78b6b73`의2183개 소스가 compile·assembly·original native 실행 전후 동일했다. 새 backend binary `3ecd1e5a`/build `cc38deb8`와339 resource·335 raw source export를 봉인했고 기존 frontend/Electron machine bytes는456개 최신 소스 pin 일치 뒤 재사용했다.
- 원래 test가 실제 frozen controller→Electron→backend epoch에서 controlled CPU A/OK/1ROI/0defect와 동일 receipt 재조회2회를 확인했다.489.168초1pass/0skip이며 정상 AppKit 종료·원래 main/backend/controller 종료를 확인했다.
- 실제 lease는 `recovery_required`를 유지한다. 전체 writer/process-tree 종료·OS installer·일반 장치 설정·post-start 환경·publisher·사람의 품질·설치 릴리스 수락은 별도 미완료이다. 초기 runner Node 부재와 private proof schema 수정 기록은 보존하며 과거 실행 성공을 새 binary로 옮기지 않았다.

## 2026-10-08 원래 backend 종료·좌표 범위·기존 연결선 편집

- clean `6f08dbd`에서 원래 ChildProcess의 직접 종료를 기다리도록 수정했다. signal 요청만으로 종료를 계산하지 않고, 동시에 호출한 stop은 같은 종료 약속을 기다린다. 원래4초 예산을 유지하며 종료가 불확실하면 후속 backend 시작을 거절한다. 직접 자식 종료 확인은 전체 writer drain 또는 process-tree 종료 확인이 아니다.
- 모든 calibration에서 양의 정수 원본 크기·polygon 경계·mask/bbox 매핑을 검사한다. 잘못된 좌표를 clip하거나 변환을 추정하지 않는다. 실제 생성 이미지 API의422·정상 경계200 및 기존 cropped-mask fixture 보존을 확인했다.
- ID 누락·중복이 있는 기존 draft에서도 선택한 연결선 한 행만 편집·삭제한다. transient 배열/행 참조는 저장하지 않고, 저장된 ID를 자동으로 고치지 않으며 유효하지 않은 runtime 게시 거절을 유지한다.
- 원래 bootstrap2개, Node39개, 좌표62개, 타입 검사3개와 실제 browser/source Electron 각1개가 통과했다. retry/skip0이며2200개 소스가 실행 전후 동일하고 소유한 GUI 프로세스의 종료를 확인했다. 초기 receipt 생성기의 경로 오타는 artifact 수집 오류로 보존했으며 실제 실행을 다시 성공한 것으로 세지 않았다.
- 원래82/156 ID, 부모 구현69/13·Windows 면제2·필수 pending11·수락0, curated 행동207개·649verified/800pending을 유지한다. 최신 frozen native·full hosted CI·사람 품질·장비 수락은 추가하지 않는다. 근거: `../verification/receipts/2026-10-08-source-safety-6f08dbd.json`.

## 프로젝트 전환과 그래프 이력 실제 재검증

- clean `1728356`의 실제 browser/Electron2건이 retry0·skip0으로 통과했다. 실행 전후2205개 tracked 입력 SHA와 clean 상태를 보존했다. 같은 데이터 폴더를 공유하는 프로젝트 전환의 원본 재조회 누락을 수정하고, A/B/A 화면의 정확한 노드·연결·현재 프로젝트 및 draft/project/image SHA를 확인했다.
- 기존 Undo/Redo의 empty·reopen·handoff6개, native keyboard delete/undo/redo의 empty·handoff2개, 검색의 handoff1개만 pending에서 verified로 변경했다. 의도한 project open2회 외 콘텐츠 저장·활성화·학습·추론 POST는0회였다.
- curated 행동207개·658verified/791pending, 부모 구현69/13·Windows 면제2·필수 pending11·수락0이다. 소스 Electron 결과를 frozen native 배포·사람 품질·장비 수락으로 승계하지 않는다. 근거: `../verification/receipts/2026-10-08-history-scope-1728356.json`.

## 영속 controller의 실제 진행 관찰 회귀

- `1f47697` hosted renderer/main 검사976pass/1fail의80ms 단일 heartbeat 표본 실패를 보존했다.160ms 간격의 제어된 fixture에서 같은 실패를 재현하고, 실제 counter 증가를 고정1초 안에 관찰하도록 테스트를 수정했다. 멈춘 fixture는 계속 실패한다. 이 재현으로 당시 hosted scheduler의 정확한 상태를 단정하지 않는다.
- 수정 후보의 원래 concurrency4 전체 renderer/main992개가 통과했고, clean `328d661`의 원래 영속 controller5개도 통과했다.2208개 tracked 입력은 clean 실행 전후 동일했다. production controller·15000ms 응답 예산·원래 자식 handle의 exit/signal 검사는 바꾸지 않았다. 최신 full hosted CI 완료·전체 writer 종료·부모 수락은 여전히 별도다. 근거: `../verification/receipts/2026-10-08-persistent-heartbeat-328d661.json`.


## 저장 평가 화면 재열기 · 61796a0

- 커밋된 소스61796a0의 browser/source Electron에서 opacity·zoom·mouse pan의 실제 close/remount 초기화와 클래스 선택의 실제 renderer reload 초기화가 각각 통과했다. 전체2222개 파일은 Git archive 및 실행 전후 해시가 같고 original/report/input/full annotation metadata와 실제 사전 복사본 bytes를 유지했다. 같은 batch의 mask2건은 nested fixture를 빠뜨린 별도 실패이며 성공으로 세지 않는다.
- 정확한 기존 reopen4개만 pending에서 verified로 바꿔 curated207개·662verified/787pending이다. 부모 구현69/13·필수 pending11·전체 수락0은 유지하며 실제 모델 품질·장비·frozen 설치 수락으로 승계하지 않는다. 근거: `../verification/receipts/2026-10-08-overlay-reopen-61796a0.json`.

### 2026-10-08 pixel mask lifecycle 실제 clean UI 검증

커밋 `17f2df6`의 실제 브라우저·source Electron 2건이 원래 timeout·0 retry로 통과했다(39.57초). Git archive와 전후2222개 source hash가 같고 original backend/main 종료·빈 teardown을 확인했다. chooser cancel/error와 preview422/503/retry/remount, zero-label export503/retry 및2개 원본의 전체 background PNG pixels·원본 mapping·실제 다운로드 hash를 검증했다. 이전617 fixture 실패는 nested 원본 경로 누락으로 보존하며 제품 실패로 세지 않는다. 기존8개 pending cell만 반영해 curated670/779이며 부모69/13·품질·장치·frozen/native acceptance는 그대로다. 근거: `docs/verification/receipts/2026-10-08-mask-lifecycle-17f2df6.json`.

### 2026-10-08 readiness·그룹 분할 preview 실제 clean UI 검증

`769e198` 실제 browser·source Electron2건은 원래 timeout·0 retry로38.38초에 통과했다. Git archive2223개 source와 전후 hash가 같고 original process 종료·빈 teardown을 확인했다. 진단503의 저장 보고서 보존·실제200 retry, 정확한 원본 SHA/UUID/revision의 라벨 화면 인계, ratio90% 실제422와 preview503/retry, unsent lot preview 재열기 후 저장 product 분할 복원5항목을 닫았다. 전체 annotation/source/split tree·diagnosis bytes·API metadata/versions/settings를 보존했고 label/split 적용은 없었다. curated675/774, 부모69/13·전체 품질/장치 수락은 유지한다. 근거: `docs/verification/receipts/2026-10-08-readiness-lifecycle-769e198.json`.

### 2026-10-08 저장 검토 큐 실제 clean UI 검증

`a09cbcb` 실제 browser·source Electron2건이 원래 timeout·0 retry로40.83초에 통과했다. 원래 Git archive2224개 source 전후 hash와 original process 종료·빈 teardown을 확인했다. 유효한 미전송 원본평가·임계값·margin의 실제 stage 이탈4건과 reload/default 복원3건, 정확한 큐 조회503 후 실제200 원본 SHA/UUID/revision 인계와 reload 재선택2건을 닫았다. 전체 원본·annotation·보고서·workbench·split bytes와 큐 cursor/history/metadata/API를 보존했고 renderer API 변경0건이다. 초기 labels와 보고서는 통제 fixture이며 사람의 truth나 inference가 아니다. curated684/765, 부모69/13·전체 수락은 유지한다. 근거: `docs/verification/receipts/2026-10-08-queue-lifecycle-a09cbcb.json`.

### 2026-10-08 원래 Node backend·preflight 종료 수명 통합 검증

`41aedeb`의 코드11개 파일은 원래 main Popen/private channel에서 인증한 Node backend의 active/direct_exited 등록과 preflight의 one-use ticket/count/original OFD 정리를 통합했다. close·wait/heartbeat·lease release가 불확실하면 custody와 active count를 유지하고 완료를 게시하지 않는다. Root193개(실패/생략0), 원래 deadline/0 retry로600.12초, 전체2240개 전후 source hash 일치 및 commit11개 code bytes와 실제 테스트 bytes 일치를 확인했다. dirty 테스트 base `a09cbcb`를 명시하며 clean 설치/native/tree/전체 writer/launch lease 해제를 주장하지 않는다. 기존 실제 backend row 기대3파일만 exact original backend/main identity로 좁게 갱신했고 private plugin 없이 통과했다. 이전 preflight cc5의 pytest 임시 증거814개 누락으로 그것은 미승인 유지하며 새1674개 증거와 red/fix/actual 기록을 별도 보존했다. 부모69/13은 유지한다. 근거: `docs/verification/receipts/2026-10-08-node-preflight-composed-41aedeb.json`.

### 2026-10-08 파생 정렬·밝기 저장 수명주기 actual clean UI8

`c969d40`의 clean browser·source Electron 파생 편집2건은 original timeout·0 retry로 통과했다. 같은68.99초 batch의 별도 queue-progress2건은 실패하여 미완료로 유지한다. Git archive2229개와 source 전후 hash, original process 종료·빈 teardown을 확인했다. invalid 각도181/밝기4.1의 저장 비활성·0 POST, 유효 미전송 설정 stage 이탈 후 기본값 복원, 각각503 보존→실제200 새 버전, reload 후 정확한 버전·원본 SHA/UUID/revision·전체 PNG·라벨 geometry·보존 출처8항목만 검증으로 올렸다. 원본/annotation/report/split 전체 tree와 API 정책·검토·설정·버전을 보존하고 workbench는 기존 seed와 의도한5파일×2 출력만 증가했다. independent PIL pixel/decoded RGB와 수학적 회전 좌표를 root 재검증했다. curated692/757, 부모69/13·전체 품질/장치/배포 수락은 유지한다. 근거: `docs/verification/receipts/2026-10-08-derived-lifecycle-c969d40.json`.
