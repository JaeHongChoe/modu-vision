# Modu-vision 작업표

확인일: 2026-10-07 · 각 실제 실행의 소스는 receipt에 고정한다. 현재 게시 원장은 `8184b75`, 마지막 Studio 팩 실행 소스는 `5284df6`이다. 아래 후속 기록은 당시 집계이며 최신 숫자는 `scripts/remaining_work_report.py`로 확인한다.

**82개 중 소프트웨어 구현 확인67개, 구현 pending15개.** Windows 전용 S6-02·S7-03은 사용자 요청 범위에서 면제하므로 계속 필요한 부모는13개다. 면제는 테스트 통과가 아니며 원래 수락 gate를 유지한다. 최종 수용 승인은0개다. 부모 pending에는 남은 코드와 서명·장비·사람 검증·실제 경과 시간 조건이 함께 들어 있다.

## 실행 순서

- 사용자 지시에 따라 S7-05의 72시간 검사는 외장 저장소의 기존 실행을 계속 유지하고, 완주 판정은 마지막에 확인한다.
- 다른 진행 가능한 구현·CI·앱 실행 검증은 이 검사 종료를 기다리지 않는다. 같은 실행의 소스·모델·시작 시각을 바꾸거나 이전 실행 시간을 합산하지 않는다.
- 현재 먼저 처리하는 항목은 S6-05의 공개 CI 실패 수정과 S7-01의 기능별 앱 검증이다. S7-05 완주 전에도 독립 결과를 기록하되 전체 수용 완료로 집계하지 않는다.

## 단계별 집계

| 단계 | 전체 | 구현 확인 | 미완료 |
|---|---:|---:|---:|
| S0 | 9 | 9 | 0 |
| S1 | 10 | 9 | 1 |
| S2 | 10 | 10 | 0 |
| S3 | 10 | 10 | 0 |
| S4 | 14 | 14 | 0 |
| S5 | 10 | 9 | 1 |
| S6 | 11 | 6 | 5 |
| S7 | 8 | 0 | 8 |

## 원래 pending15개 · 요청 범위13개

| ID | 작업 | 실제 남은 조건 |
|---|---|---|
| S1-08 | 기존 JSON과 DB migration | 원본 이력 보존·forward recovery와 명시적으로 참여한 POSIX basic/SSH/specialist worker의 live cutover는 검증됨. 지원하지 않는 기존 worker는 종료 후 이관한다. 기존 설치 home의 명시적 인수와 native target 이관·독립 수락이 후속 조건이다. |
| S5-01 | 독립 검사 서비스 | 독립 서비스의 SCM 등록·권한·Session0·재부팅과 실제 장치 검증 필요. |
| S6-02 | Windows CPU 설치 프로그램 | 현재 Windows unsigned NSIS/portable 빌드·CPU 패키지 실행은 통과. non-admin 설치·제거·SCM 분리와 실제 설치 QA는 미검증/면제. |
| S6-03 | 선택형 GPU와 runtime pack | pydicom·OpenVINO·CUDA12·CTC의 개별 범위 근거를 유지한다. Studio의 프로젝트별 비활성 설치·별도 pin·실제 변조 거절·재시도·재열기는 clean5284df6 browser2/native3, backend83, renderer907개로 검증했다. 자동 활성화·frozen/native 배포 조합·실 publisher·대표 품질은 별도 조건이다. |
| S6-04 | 오프라인 설치와 업데이트 | owned drained POSIX 앱/DB 통합 cutover·복구 및 darwin schema2 app 구조는 구현됨. 기존 설치 home 인수, 업데이트 앱의 영속 실행 소유권·native caller·기준 이미지 인수인계와 OS installer adapter를 이어간다. 실 publisher 서명은 별도 조건이다. |
| S6-05 | 공개 CI와 source 재현성 | 실제 이전 소스의 hosted 성공과 실패를 보존한다. 현재 게시8184b75 및 다음 변경의 전체 hosted 결과가 필요하다. 과거 소스 성공을 새 source에 넘겨 집계하지 않는다. |
| S6-06 | 서명과 릴리스 채널 | 실제 Authenticode/publisher identity·서명 후 byte inventory·채널 승인 필요. |
| S7-01 | 기존 기능 전체 coverage 계약 | 원래156개 ID를 유지한다. 현재199개 curated action의545개 시나리오 검증,848개 pending이다. 누락 메뉴/shortcut·남은 행동 시나리오·전체 기능 수락을 이어간다. 이 숫자를 부모 완료 수에 더하지 않는다. |
| S7-02 | 10모델군 실제 작업 시나리오 | 실제5 distinct learned chain은 완료. 10모델군 각각 사람이 검토한 정답과 대표 multi-task recipe 검증 필요. |
| S7-03 | Windows 실제 설치와 사용 QA | Windows 실제 설치·사용 QA 사용자 면제. hosted Server2025 component 실행을 Win11 실사용 성공으로 세지 않음. |
| S7-04 | 팀 동시 작업과 fault injection | 실제 HTTPS 사용자 충돌·권한 철회·재시작과 로컬 장애 matrix, portable 앱/DB 복구는 범위별로 검증됨. 실제 target 물리 장애와 실 publisher 서명 native update는 미검증이다. |
| S7-05 | 데이터 규모와 연속 운전 | 100k/큰 원본·queue component 검증됨. 내부 저장소 47회 실행은 disk reserve에서 종료됐고 기록 보존. a184b1e 고정 소스로 외장 APFS 저장소에서 새 72시간 실행 중. 이전 시간을 합산하지 않고 완주 receipt 필요. |
| S7-06 | 공정 품질 승인과 장비 검증 | 사용자 지정 공정 미검/과검 정책·대표 truth·카메라/PLC/MES 실제 장비 승인 필요. |
| S7-07 | 공개 후보와 릴리스 판정 | 실제 DICOM 지원 조합과 source-bound 개발 의존성590개/미해결53개·6개 배포 범위 hold 판정 기록. 실제 shipped source/SBOM/notices·서명·독립 검토·공정 품질·첫 사용자 파일럿은 미완료. 개발 환경 inventory로 배포 증거를 대신하지 않음. |
| S7-08 | 파일럿 feedback과 지속 유지 | 첫 사용자의 설명 없는 파일럿 및 실제 운영 책임·지원/SLA/유지보수 주기 필요. |

## 소프트웨어 구현 확인67개

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
| S5-08 | drift·검수·재학습·비교와 명시적 승인 후 후보 서비스 적용 |
| S5-09 | 백업과 복구 및 보존기한 |
| S5-10 | 서비스 보안과 운영 설정 |
| S6-01 | 오픈소스와 모델 라이선스 정책 |
| S6-07 | 외부 기여와 프로젝트 운영 |
| S6-08 | SDK와 자동화 API |
| S6-09 | 공개 위생과 지원 자료 |
| S6-10 | 브라우저 및 확장 개발 경계 |
| S0-09 | 초기 UI와 Electron 검증 환경 |
| S6-11 | 공개 배포 라이선스 결정 |

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
