# 내장 오픈소스 엔진 검증

기준: `feat/embedded-oss-20261114`, 공통 CLI PR27의 `fbc9e3b95717466601147ecf8ac05eca9d597a6c` 위 별도 기능 변경. 설치 앱·현재 실행 중인 웹을 교체하지 않았다. 실제 제조 자료·사용자 프로젝트·계정·GPU·모델·다른 체크아웃·자동화를 변경하지 않았다.

## 구현과 확인

| 범위 | 확인한 것 | 구분할 한계 |
| --- | --- | --- |
| cleanlab 선택 계산 | 공식 2.9.0 wheel/rank.py 해시, 원본 함수 AST와 17개 수치 대조, 최대 차이 0 | cleanlab 전체 제품·Datalab을 이식한 것이 아님 |
| 저장 평가 → 라벨 검토 | 확률 행렬·top1·confidence·클래스/자료/정답 식별자 검증, 실제 분석·큐·다시 열기·revision 저장 | 점수는 참고 순위. 오라벨 확정·LOT 독립성·품질 승인 아님 |
| Optuna 5 | 실제 TPE ask/tell, 같은 초기 4개 후보 후 합성 목적값에 따라 후속 제안이 달라짐. 중복·예산·취소·완료 영수증·원격 불확실성·설정 재실행 검사 | 모델은 fake runner. 실제 학습·GPU·제조 성능 측정 없음. pruning/정확 optimizer 재개 없음 |
| 검출 AP v2 | 실제 pycocotools 2.0.11 결과와 합성 bbox 대조. 없는 GT 클래스·이미 사용한 GT·동률·IoU 경계값·top100·정답 없음 검사 | bbox 전체 면적/noncrowd/nonignored 범위. COCO 전체 기능·모델 개선 주장 아님 |
| 평가 연결 | 일반/공통 코호트/원격 요청의 AP 버전을 식별자에 고정, 결과 버전 대조. 원본 이미지 좌표의 동일 입력으로 계산/검증 | v2는 원본 정답 로더를 위해 추가 CPU 이미지 decode 수행. 과거 계약은 그대로 읽음 |
| 로컬 OpenTelemetry | 실제 SDK로 도우미 읽기 → API wrapper → 원래 인증된 ASGI → 기존 진단의 인과 ID·시간 확인. 반환/오류 객체 보존·범위 A→B→A·권한·상한·누락 검사 | socket 없는 owned ASGI bridge. 실계정 GPT·native/remote/비동기 worker 인과 연결은 검증하지 않음 |
| 흐름 편집 | 실제 Zustand history/semantic key 검사. 실제 브라우저에서 방향키·연속 이동 한 번 undo·Shift·검색 focus·canvas pan·port Escape | 기존 DesktopHostShim 사용. 실제 모델·설치 Electron·현장 작업 아님 |
| 패키지 | JS 정확 버전, Linux/Windows Python3.13 CPU hash lock, 필수 빌드 의존성·동적 import·metadata·원본 라이선스 리소스·고지 | 새 frozen executable 또는 모든 OS 설치를 만든/검증한 것은 아님 |

원본 수치 대조: [cleanlab](receipts/embedded-oss/cleanlab-parity.json), [실제 Optuna 합성 피드백](receipts/embedded-oss/optuna-feedback.json). COCO 전체 입력과 기대값은 [`detection_ap_v2_coco.json`](../../backend/tests/fixtures/detection_ap_v2_coco.json)에 보관하며 [`generate_detection_ap_oracle.py`](../../scripts/generate_detection_ap_oracle.py)로 개발용 isolated pycocotools에서 재생성한다. 사용자 runtime에는 pycocotools를 요구하지 않는다.

## 로컬 검사 결과

- 핵심 백엔드 묶음: **235 passed**. 새 분석·immutable backup·실제 sampler·trace·패키징과 기존 CLI/HTTP/기능 목록/라이선스 회귀를 함께 실행했다.
- 검출·공통 코호트·일반 원격 평가/제어 묶음: **168 passed**. 마지막 추가 계약 거절 검사 포함 focused **15 passed**.
- 기존 source CI 계약: **50 passed**. `.github/workflows/ci.yml`, `ci-studio-cli.yml`은 부모 기준과 동일하다. 새 검사는 별도 `ci-embedded-oss.yml`에 추가했다.
- 새 additive workflow의 정확한 CPU 검사 명령도 로컬에서 **126 passed**. 기능 목록·원격 버전·원본 좌표·frozen source resource 검사를 한 프로세스에서 함께 실행했다.
- 최종 동일 소스 렌더러 전체 **3,135 passed / 1 skipped / 0 failed**. 실제 도우미-ASGI bridge도 실행했다. skip은 별도 opt-in인 compiled Electron child 검사이며 실행한 것으로 세지 않았다. focused 흐름 검사는 **69 passed**다.
- TypeScript renderer/main 및 E2E typecheck, API 생성물 현재성, Vite/Electron source build, light-theme 생성물 현재성 확인. Vite의 기존 큰 chunk 경고는 남아 있다.
- 엄격 UI 정적 점검의 기존 다른 화면 **38건**을 구분했다. 이번 변경 파일의 신규 위반은 없으며 제품 전체 UI가 정리됐다고 주장하지 않는다.
- service/action 근거 무결성 검사 통과. 변경된 4개 화면의 현재 소스 선언만 [별도 delta](receipts/2026-11-14-embedded-oss-source-declarations.json)로 갱신했고 역사 실행·승인 record는 동일하다. `accepted_features:0`, 기존 verified1011/pending438도 유지했다. action inventory/라이선스 파일 수집 검사 **13 passed**.

## 실제 브라우저

- 라벨 재확인 **1 passed, 43.4초**: 실제 API와 DOM으로 `.51`의 올바른 NG 예측이 먼저 나오는 순위, 참고 문구, 저장 분석/큐, 실제 현재 라벨 수정 후 새 큐409, 역사 기준 안내, 검토자와 cursor1 재접속을 확인했다. 원본 이미지 6장과 저장 분석/평가 해시가 유지됐다.
- 평가 확률·모델/학습 영수증 식별자는 합성으로 준비했다. 자료 버전·코호트 동결·분석·큐·현재 라벨 수정과 UI 저장/재접속은 실제 코드로 수행했다. 모델 추론 결과로 혼동하지 않는다.
- 다크 1440×900, 라이트 980×820 흐름 키보드 **2 passed, 31.7초**. 저장 graph/합성 원본 해시는 같았다.
- 기존 DesktopHostShim을 사용했으며 API route mock은 사용하지 않았다. owned backend 종료·port 해제를 확인했다. 라벨 검토에서 재접속/종료 중 resource network console 오류 6개가 기록됐으나 page error는 없었다. console0이라고 주장하지 않는다. 검토 패널의 일부 아래쪽 내용은 내부 스크롤 영역에 있으며 DOM assertion과 저장 영수증으로 대조했다.

## 보존한 실패 이력

1. 이전 부모 CLI 빠른 CI의 보호된 전체 명령 수정으로 발생한 2개 실패는 별도 CLI 보완 commit에서 복구됐다. 기존 전체 회귀 명령·해시 보호를 변경하지 않았으며 이 변경은 additive lane이다.
2. `test_original_current_project_request.py`의 기존 `.matches` fixture 4개 실패는 pristine 부모에서도 재현됐다. FastAPI lazy route 가정 문제이며 원래 observer·권한 검사·해당 단언을 고치지 않았다.
3. `test_release_delivery.py::test_frozen_data_tree_builds_an_importable_remote_worker_archive`의 두 경로 구분자 경우는 pristine 부모에서도 실패했다. fixture는 C 소스까지 포함한 resource 개수와 `.py`만 센 diagnostic 개수를 비교한다. 두 버전 모두 실제 remote source archive import/checksum 검사는 통과하고 마지막 개수 단언에서 실패했다. 이 기록을 전체 통과로 지우지 않았다.
4. 개발 중 새 import를 처리하지 못한 테스트 loader, theme 생성물, IoU float32 경계값, remote AP 버전 누락, 원본 좌표 재현 차이, immutable backup의 과도한 historical 예외는 RED를 보존하고 수정·재검증했다.

## 최종 확인과 배포 경계

독립 전체 브랜치 검토에서 확인된 P1/P2 지적은 없었다. 검토자가 별도로 **108 backend + 17 renderer** 검사를 실행했으며 실제 도우미-ASGI 인과 연결과 최종 원본 좌표 경계 사례를 포함했다. 최종 renderer/main typecheck·build·API 생성물·E2E typecheck·diff 검사도 통과했다. 기능별 검증 수치는 겹치는 검사가 있으므로 합산하지 않는다.

부모 PR27의 정확한 `fbc9e3b9`는 현재 빠른 검사·CLI 검사가 성공했다. exact-head full은 기동하지 않았으며 이에 대한 readiness gate 실패는 보존했다. 이번 브랜치도 원격 CI·전체 회귀·main 병합을 완료했다고 주장하지 않는다. 실제 실행 앱/웹 교체·동결 설치·현장 정답 수락은 이 로컬 검사에 포함되지 않는다. 기존 DINO/YOLO·native library 등의 미해결 배포 라이선스 판단도 이번 permissive 라이브러리 추가로 해결됐다고 보지 않는다.
