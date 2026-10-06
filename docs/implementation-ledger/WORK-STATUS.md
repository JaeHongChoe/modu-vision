# Modu-vision 작업표

확인일: 2026-10-07 · 기준 공개 소스 `16395a47c7afdd1f24c0300d4b88fe40e636dc60`와 기록된 후속 검증

**82개 중 소프트웨어 구현 확인66개, 구현 미완료16개.** 이번 실제 검증으로 원래25개 중9개를 추가 확인했다. Windows 실제 설치·사용 QA(S7-03)는 사용자 면제이므로 진행 대상은15개다. 최종 수용 승인은0개다.

## 단계별 집계

| 단계 | 전체 | 구현 확인 | 미완료 |
|---|---:|---:|---:|
| S0 | 9 | 9 | 0 |
| S1 | 10 | 9 | 1 |
| S2 | 10 | 10 | 0 |
| S3 | 10 | 10 | 0 |
| S4 | 14 | 14 | 0 |
| S5 | 10 | 8 | 2 |
| S6 | 11 | 6 | 5 |
| S7 | 8 | 0 | 8 |

## 미완료16개

| ID | 작업 | 실제 남은 조건 |
|---|---|---|
| S1-08 | 기존 JSON과 DB migration | 종료된 로컬·remote 이력과 현재 recovery index의 cutover·forward recovery·새 manager 복구 검증됨. 정확한 기존 ledger/lease 두 schema의 원본 보존 변환·복구 112개 통과. 살아 있는 worker·불확실 lease·다른 구형 schema 이관 필요. |
| S5-01 | 독립 검사 서비스 | 독립 서비스의 SCM 등록·권한·Session0·재부팅과 실제 장치 검증 필요. |
| S5-10 | 서비스 보안과 운영 설정 | HTTPS/credential/격리 계약과 별개로 실제 운영 비밀 저장·권한·감사 정책 검증 필요. |
| S6-02 | Windows CPU 설치 프로그램 | 현재 Windows unsigned NSIS/portable 빌드·CPU 패키지 실행은 통과. non-admin 설치·제거·SCM 분리와 실제 설치 QA는 미검증/면제. |
| S6-03 | 선택형 GPU와 runtime pack | 검증된 inventory의 원자적 비활성 설치/재설치/경쟁·변조 거절은 완료. 실제 배포 pack·대상 변환/추론·지원 조합 측정은 미완료. |
| S6-04 | 오프라인 설치와 업데이트 | offline checksum/authority 및 실제 frozen known-image은 통과. DB 복구 전·후 강제 종료/새 CLI 재시도·변조/새 쓰기 보존 관련116개 통과. app 설치와 DB의 통합 cutover·실제 publisher 신뢰 설정 필요. |
| S6-05 | 공개 CI와 source 재현성 | a184b1e hosted CI 성공: CPU 2198통과·7skip, browser 162통과·1skip. 소스 해시 누락 수정과 95개 선언된 Git 소스 대조 완료. CI 출력 때문에 dirty로 기록된 GUI 162건은 clean으로 승격하지 않음. 출력 외부 경로 수정·관련 56개 통과. 수정된 hosted 결과·skip 조건 확인 필요. |
| S6-06 | 서명과 릴리스 채널 | 실제 Authenticode/publisher identity·서명 후 byte inventory·채널 승인 필요. |
| S7-01 | 기존 기능 전체 coverage 계약 | 깨끗한 4062173 소스에서 실제 Electron 개선 전체 흐름 1건·129개 artifact hash와 엄격한 GUI 기록 확보. 156개 기능의 action별 성공/오류/취소/재열기/이관 근거 원장 보완 필요. 단순 매핑이나 1건의 완주는 전체 수용 완료가 아님. |
| S7-02 | 10모델군 실제 작업 시나리오 | 실제5 distinct learned chain은 완료. 10모델군 각각 사람이 검토한 정답과 대표 multi-task recipe 검증 필요. |
| S7-03 | Windows 실제 설치와 사용 QA | Windows 실제 설치·사용 QA 사용자 면제. hosted Server2025 component 실행을 Win11 실사용 성공으로 세지 않음. |
| S7-04 | 팀 동시 작업과 fault injection | 실제 HTTPS 두 사용자 충돌/권한 철회/재시작 2건과 10개 fault 시나리오 40건 통과. 실제 target 및 signed installer/DB cutover는 미완료. |
| S7-05 | 데이터 규모와 연속 운전 | 100k/큰 원본·queue component 검증됨. 내부 저장소 47회 실행은 disk reserve에서 종료됐고 기록 보존. a184b1e 고정 소스로 외장 APFS 저장소에서 새 72시간 실행 중. 이전 시간을 합산하지 않고 완주 receipt 필요. |
| S7-06 | 공정 품질 승인과 장비 검증 | 사용자 지정 공정 미검/과검 정책·대표 truth·카메라/PLC/MES 실제 장비 승인 필요. |
| S7-07 | 공개 후보와 릴리스 판정 | 최종 지원 조합/known issues/license/SBOM/서명/업데이트/파일럿 입력을 모아 공개 후보 판정 필요. |
| S7-08 | 파일럿 feedback과 지속 유지 | 첫 사용자의 설명 없는 파일럿 및 실제 운영 책임·지원/SLA/유지보수 주기 필요. |

## 소프트웨어 구현 확인66개

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
| S6-01 | 오픈소스와 모델 라이선스 정책 |
| S6-07 | 외부 기여와 프로젝트 운영 |
| S6-08 | SDK와 자동화 API |
| S6-09 | 공개 위생과 지원 자료 |
| S6-10 | 브라우저 및 확장 개발 경계 |
| S0-09 | 초기 UI와 Electron 검증 환경 |
| S6-11 | 공개 배포 라이선스 결정 |

## 이번 실행 근거

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
CI preserves pending as well as running exact-source qualifications. The
independent external72-hour run keeps its original source and duration.
