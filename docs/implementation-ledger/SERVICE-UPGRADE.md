# 서비스 고도화 실행 기록

기준 계획: [82개 작업](../superpowers/plans/2026-10-02-windows-open-source-service-upgrade.md), [작업 상태](../service-upgrade-program.json), [기존 기능 연결](../service-upgrade-coverage.md).

기준 소스: `03e8f6d142e6f8e7fa6bd80cb82a4a9ccf8d65e6`. 목표 플랫폼은 Windows 11 x64이며, 현재 실행 근거는 macOS 개발 환경에서 수집했다. 구현·계약 테스트·화면·저장 및 재열기·실제 대상 실행·서명·모델 품질을 각각 기록한다.

## 2026-10-02 구현 회차

| 작업 | 실제 변경과 검증 | 남은 확인 |
| --- | --- | --- |
| S0-01 | 실제 데이터 로더에서 이미지·박스·마스크 동시 증강. 일반 DET 리사이즈·LabelMe dispatch·미선언 callback·빈 geometry 보강 후 153개 통과·실제 데이터 opt-in 3개 skip. 독립 재검토 통과. | 회전 입력의 경계 검증과 provenance 상세 기록은 S4 이전 보강 항목. Windows·전체 학습 및 품질 회귀. |
| S0-02 | 모델 임계값·단위·교정 ID를 평가·추론·플로우·비교·원격 worker·화면에 전달. 반올림 전 원시 점수로 판정하며 체크포인트 교체 시 교정을 다시 읽음. 독립 검토 통과. 백엔드 244개 통과·선택 의존성 1개 skip, 화면 로직 39개 통과, typecheck 통과. | 실제 임계값 8 화면 편집·저장·재열기·실행, 실제 원격 대상과 Windows 확인. |
| S0-03 | 정답·클래스 역할·프로젝트 출처·라벨 세트 변경 후 오래된 승인·내보내기·stage 차단. 변경과 승인 사이의 경쟁 조건을 mutation fencing으로 처리. 독립 검토 통과. 최신 관련 테스트 39개와 기존 평가·승인 테스트 36개 통과. | Windows와 실제 대상 실행. |
| S0-04 | 중앙 적용·롤백의 공통 승인 검사와 거절 기록. 비상 롤백에 인증된 소유자·사유·변경 불가 감사기록 요구. 철회된 승인에는 remote command 0회. 백엔드 67개 통과. 새 화면 3개 브라우저·8개 helper 통과, 최종 권한 갱신을 독립 재검증. | 실제 원격 장비 및 Windows 실행. transport 검증은 fixture 사용. |
| S0-05 | 의미·배치 버전, 과거 평가 표시, 원본 viewer, 명시적 task 변경 결과와 실패 범위를 보강. 실제 TSX 소비자 7개·과거 crop 표시 3개·관련 renderer 24개 통과 및 독립 검토. 임계값 8·원본 열기 등을 브라우저 fixture에서 확인. | 프로젝트 경계 29개·공동 원본 17개 독립 검토 통과. 전체 renderer 234개 통과. Windows·실제 모델 판정은 남아 있음. |
| S0-06 | 작업 복구·PID 재사용·불확실 ownership·검사 실패 quarantine·frozen entrypoint·IPC/sandbox·HTTPS/CSP 보강. 약한 boot-time 추정이 살아 있는 token 일치 프로세스를 종료로 오인하던 결함도 수정하고 독립 재현 확인. 16개 테스트, 142개 회귀(1 skip), 브라우저/실제 Electron 4개 통과. | Windows native, 재빌드한 frozen binary, 실서버 실행. PID 재사용은 격리 fixture 근거. |
| S0-09 | 브라우저와 실제 Electron의 전용 프로젝트·프로세스·포트·receipt·스크린샷·정리 환경 추가. 최신 4개 실행, Node 13개·typecheck 통과. 19개 소스 및 receipt hash 일치, preflight 문제 0·잔여 프로세스 0 확인. | Windows native·installer 및 재빌드한 frozen binary. |
| S0-07 | 과거 모델·플로우·평가·승인의 증강 정합과 점수 단위 영향을 안내하는 API. 독립 검토·29개 테스트 통과. 기존 두 모델 metadata 확인. 안내 화면을 연결하고 보고서의 늦은 응답 방지, 4개 브라우저와 독립 재현 통과. | 실제 복구·재평가 실행. 읽기 전용 SQLite 접근은 shared-memory 접근 시각에 영향을 줄 수 있음. |
| S2-02 | 공통 폼·상태·비동기 동작·dialog focus와 영향 안내 디자인 기반. 1366×768·1920×1080, 현재/과거 보고서 응답 순서 검증 포함 브라우저 4개·Node 4개·typecheck 통과, 11개 파일 독립 검토 통과. | flow canvas·ROI 좌표·노드 키보드 편집·Windows native DPI는 후속 acceptance. |
| S1-01 | SQLite의 workspace·project·actor 문맥과 artifact 참조, local/team 동시 요청·이벤트·복원본 분리. 새 CPU 환경의 관련 백엔드 118개·renderer 39개 통과. 거부된 화면 전환 뒤 저장 대상과 이전 telemetry 연결 유지 문제를 수정하고, 독립 원래 재현·인증 WS 9개를 통과. | 수정된 실제 API·store·socket 소스 독립 검토 및 hash 일치. Windows native는 남아 있음. |
| S1-02 | 영속 작업 원장·멱등 제출·시도·취소 의도의 RED 이후 job state/store와 실제 학습 route 연결 구현 중. 이 게시 후보에는 진행 중인 원장 소스를 넣지 않음. | 실제 route·종료·재시작·detach 검증과 독립 검토. 아직 구현 완료 근거 없음. |
| S1-06 | checksum 검증 후 원자적 게시, 프로젝트·actor별 재개·quota, 참조·backup pin·GC, 권한 철회 재검사, 원본 복사 보호. 70개 통과 및 독립 재검토. fsync 뒤 rename 전 종료한 다운로드는 서버 검증을 다시 받아 재개하며 EOF 복구·권한 거절 5개 독립 통과. | 실제 NAS/S3 대상과 Windows 확인. S3는 fake client 근거이며 SDK 미설치 시 명시적 unavailable. GUI 연결은 후속 항목. |
| S6-05 | Linux CPU·Windows Server native CI 소스, 79개 hash 고정 의존성씩, 계획 누락 검사와 실행 receipt. Electron 44.5.1·builder 26.17.0의 격리 환경에서 type/build/실제 macOS Electron smoke 통과, npm audit 0. CI 소스 8개·lock 5개 독립 검토. 새 macOS CPU 환경의 관련 backend 267개 통과. | 실제 Actions의 Linux/Windows 실행, Windows 11 설치, release workflow·서명·공개 binary 결정. 공유 개발 환경의 설치된 Electron은 아직 이전 버전. |
| S6-01 | 실제 의존성·가중치별 라이선스 inventory와 배포 조건 기록. 미확인 WITH 예외·괄호 오류를 unresolved로 보존하고 최종 frozen receipt 갱신. 62개 테스트·notice check 통과, 독립 16개 재현·4개 소스·6개 receipt·8개 입력 hash 확인. | 실제 배포 패키지의 라이선스 원문·미확인 native/모듈 해결과 공개 배포 결정은 S6-11. |

현재 핵심 백엔드 통합 명령에서 **331개 통과·4개 skip**을 확인했다. 이 명령은 증강·점수·승인·중앙 배포·영향 조사와 관련 기존 로더·평가·플로우 suite를 함께 실행했다. 3개는 실제 데이터 opt-in 조건, 1개는 선택 의존성 조건이었다. 추가 화면·복구 변경은 해당 task의 별도 회귀를 거친다.

각 테스트 수는 해당 명령의 결과다. 서로 겹치는 suite를 합산해 총 검증 수로 사용하지 않는다. 위 작업의 구현 상태는 전체 프로그램 완료를 뜻하지 않는다.

새 CPU 환경의 267개 명령은 증강·점수·승인·중앙 롤백·복구·프로젝트 문맥·artifact 저장소·경로 alias·라이선스·계획·CI lock 계약을 실행했다. 이후 프로젝트 전환의 HTTP/WS 수정은 별도 최종 재현으로 판정한다. 네이티브 Windows·실장비·모델 품질 근거로 확대하지 않는다.

### 실제 데이터 확인

사용자가 제공한 로컬 QC 이미지 1개와 polygon 라벨을 별도 검증 fixture에서 읽었다. 원본 8192×5464 입력을 640×427 로더 입력으로 확인했으며, 이미지 반전·박스 반전·마스크 반전이 예상 결과와 정확히 일치했다. 마스크와 예상 변환의 IoU는 1.0이었다. 원본 171개 파일의 목록·크기·수정 시각과 선택한 이미지·라벨 SHA-256을 전후 비교했다. 이 확인의 범위는 데이터 로더의 좌표 정합이며 모델 학습·품질·전체 화면 QA 근거는 별도로 수집한다. 원본 자료와 상세 결과는 공개 저장소에 포함하지 않는다.

### 회귀 명령

```sh
python -m pytest backend/tests/test_service_joint_augmentation.py backend/tests/test_dataset_loaders.py backend/tests/test_grouped_dataset_views.py backend/tests/test_dataset_import_contract.py backend/tests/test_dataset_multiclass_gallery.py backend/tests/test_automl_trainer.py::TestIndustrialAugmentations -q
python -m pytest backend/tests/test_service_release_eligibility.py backend/tests/test_fleet_emergency_rollback.py -q
python -m pytest backend/tests/test_service_score_contract.py -q
node --test scripts/tests/e2e-harness.test.cjs
npx playwright test scripts/e2e/fixtures/harness-smoke.spec.ts
```

## 추가 기능 계약

E01–E08은 [설계](../superpowers/specs/2026-10-02-windows-open-source-service-design.md)의 확장 acceptance를 따른다. 다중 시점 검사, 기준 특징 ROI, 장비 설정에 연결된 치수 교정, 규칙 변경 이력, 정답 샘플 라벨 QA, 수집량 제한, 배포 의존성 점검, 데이터 입출력 작업 복구를 기존 담당 작업에 연결했다. 현재는 계획 상태이며 실제 구현과 실행 근거가 생길 때 상태를 바꾼다.

## 작업 루프

담당 파일 확인 → production 경로에서 실패 재현 → 실제 구현 → 독립 검토 → 관련 테스트·화면·실행 결과 확인 → 상태와 근거 기록 → 다음 의존성 충족 작업으로 진행한다. 미충족 외부 조건은 담당과 조건을 기록하고 구현 가능한 작업을 이어간다.

### Git 반영

검증된 핵심 수정과 실행 계획은 `main`의 `19c2f5fe2c030891c3ae06908e893e6e8e38ece9`에 반영하고 원격 HEAD를 확인했다. 해당 게시본을 별도 tree로 구성해 앱·main typecheck를 통과시켰다. 라이선스 inventory는 `423e3fcfb15550ceadb2e3396deeb94893c941ca`에 게시하고 원격 HEAD를 확인했다. S0-05·S1-01·S1-06·CI 후속 변경은 작업 tree의 후보이며, 최종 문맥 경계 검증과 해당 파일 hash 확인 후 반영한다.

### 게시 후보의 통합 확인

승인된 54개 파일을 index에서 별도 tree로 구성했다. 새 Electron 44.5.1 의존성으로 typecheck·E2E typecheck·build와 renderer 227개를 통과했다. Chrome의 플로우 화면 10개와 실제 macOS Electron 전용 profile·backend 시작 1개를 통과했다. 처음 browser preflight는 새 bundled Chromium 미설치로 실행 전에 실패했으며, 설치된 Chrome channel로 다시 실행했다. 브라우저의 모델 응답은 fixture이며 실제 학습 품질 근거가 아니다. 진행 중인 영속 작업 원장 소스는 이 후보에서 제외했다.

### 소스 게시 및 hosted 검사 수정

54개 검토된 파일은 main의 e02b00d56b6adbcc76cc2580321c91676974ad83에 게시했고 원격 HEAD 일치를 확인했다. 첫 Linux/Windows Actions 실행은 job env에서 runner.temp를 사용한 설정 오류로 실제 job 시작 전에 거절됐다. artifact 임시 경로를 사용 step의 env로 이동하고, 실패를 재현하는 회귀와 독립 7개 테스트를 통과했다. hosted 재실행 및 Windows 실행 근거는 아직 pending이다. 영속 작업 원장·계정/권한 source는 별도 진행 중이며 이 게시본에 섞지 않았다. 단계별 다음 행동 안내도 다음 설계 항목으로 시작했다.

### Hosted 환경의 화면 fixture 보강

main4542288 재실행에서 Linux·Windows Server CPU 의존성 hash 설치와 npm 설치·타입 검사가 성공했다. Node 24의 화면 회귀는 225개 통과·2개 실패였으며, 두 layout 테스트의 브라우저 localStorage fixture 누락이 원인이었다. 실제 component render 범위에만 메모리 storage를 두고 원래 global descriptor를 finally에서 복구했다. global이 없는 경우와 기존 getter가 있는 경우 각각 2개 독립 통과. product 동작과 기존 assertion은 그대로이며 실제 hosted 후속 검사 완료는 아직 주장하지 않는다.

다음 사용자 화면 작업은 단계별 부족한 입력·해결 위치·권장 행동, 작업 종류 변경 전 영향 미리보기다. 실제로 mount된 wizard/WizardFooter를 확장하며 이전 결과 보기와 수동 이동을 보존한다. 저장소·학습 원장·권한 구현과 별도 파일로 진행한다.
