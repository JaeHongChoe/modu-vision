# 초보자 시작·학습 동선 검증

기준: `332adfcce7d86953d92aaf69fc009dad69cf6ed5` 위 `feat/novice-first-20261114`. 기존 내장 OSS PR #28 위의 별도 변경이다. 개발 작업트리에서 검증했으며 현재 설치 앱·켜진 웹·main은 교체하지 않았다.

이 문서는 첫 구현 `81b61d4a`의 검사 기록이다. 이후 UI·UX 정리와 최종 검사·실패 수정 이력은 [UI 정리 검증](2026-11-14-novice-ui-polish.md)에 별도로 기록했다.

## 구현과 확인

- 시작 안내의 예제 / 내 사진 / 기존 작업 진입점을 기존 실행·프로젝트 관리에 연결했다. 예제 실행기는 변경하지 않았으며 중복 설명을 줄였다.
- 새 프로젝트의 간단히 보기에서는 원하는 결과를 명시적으로 골라야 한다. 미선택·아직 모르겠어요는 생성을 허용하지 않는다. 네 목적은 기존 classification/detection/segmentation/anomaly에 대응한다.
- 네 사용자 목표 아래 기존 여섯 단계에 모두 접근할 수 있다. 완료 표시는 기존 단계 근거를 사용하고 이동 버튼은 실제 실행으로 표현하지 않는다.
- 현재 작업 카드는 기존 도우미/자동 진행 컨트롤러의 상태를 읽는다. 실행·장치 대기·입력 부족·실패·일시정지·결과 확인을 구분한다. 다른 프로젝트의 로그와 늦은 오류는 표시에서 제외한다. 새 실행기나 polling loop를 추가하지 않았다.
- 학습 준비는 부족한 입력과 해결 버튼을 먼저 보여 준다. 사진 없음과 정답 없음, 모델·서버·예약 설정 오류의 해결 위치를 구분한다. 사진 변환은 간단히 보기에서 접고 모두 보기에서는 기존 순서를 유지한다. 보기 전환 중 편집기 identity는 유지한다.
- 오래 학습하면 정확해진다는 문구, 일정 시험 장수면 믿을 수 있다는 문구를 제거했다. 실제 시작 조건·권한·실행 소유권·정답/시험 세트 계약은 유지했다.

## 실행한 검사

| 검사 | 결과 |
| --- | --- |
| `npm run test:renderer` | **3,183 passed / 0 failed / 2 skipped**, 전체 3,185개 |
| `npm run typecheck` | renderer 및 main TypeScript 통과 |
| `npm run typecheck:e2e` | 통과 |
| 격리 browser `novice-first-journey.spec.ts` | **1 passed**, 실제 renderer와 소유 backend 사용 |
| renderer production build | browser fixture의 새 격리 빌드 통과 |
| 밝은 테마 생성 / `git diff --check` | 통과 |
| 독립 소스 리뷰 | 발견한 상태·복구·표시 범위 문제 수정 후 열린 중요 지적 없음 |

Renderer skip 두 개는 opt-in compiled Electron bridge smoke와 owned assistant→ASGI integration이다. 이 변경에서 실행한 검사로 계산하지 않는다. Backend 전체 회귀·원격 CI 결과는 이 표에 포함하지 않는다.

브라우저는 합성 scratch 프로젝트에서 처음 안내와 최근 작업 진입, 명시적 목적 선택 전 생성 차단, 영역 분할 프로젝트 생성과 backend readback, 네 목표/내부 학습 단계 이동, 빈 사진 복구 안내, 기본 접힌 사진 변환, 모두 보기의 기존 기능 노출을 확인했다. 1440×900 및 1100×780, 밝은/어두운 테마를 캡처하고 직접 읽었다. 좁은 화면의 가로 넘침도 확인했다. 테마 전환 애니메이션이 끝난 뒤 캡처하며 고정 sleep은 넣지 않았다.

## 실패·수정 이력과 근거 위치

- 처음 browser 시도는 UI 전에 공유 Python 환경의 `opentelemetry` 미설치로 종료됐다. 공유 환경을 고치지 않고 작업 소유 QA venv에 저장소가 사용하는 SDK를 설치해 실행했다.
- 다음 시도는 간단히 보기의 닫힌 더 보기 메뉴 안 버튼을 바로 찾던 테스트 동선에서 timeout 됐다. 실제 메뉴를 먼저 여는 동선으로 수정했다.
- 중간 renderer 전체 실행의 두 header fixture 실패는 새 탐색 helper를 가짜 함수로 대체하던 mock 때문이었다. 실제 helper를 로드하도록 수정하고 기존 프로젝트 전환/실패 가드 단언을 유지했다.
- 테스트를 먼저 실패시켜 확인한 결함에는 미선택 목적 생성, 다른 프로젝트 진행 로그, 완료 기록에 가려진 대기 작업, 입력 대기로 보이는 실제 실패, 잘못된 복구 화면 등이 포함된다.
- 로컬 상세 로그·리뷰: `.superpowers/sdd/2026-11-14-novice-first/`. `renderer-verified-final.log`, `typecheck-final.log`, `e2e-types-final.log`, `browser-verified.log`, `start-learning-review.md`, `verified-source-sha256.json`에 최종 근거를 보존한다. 이전 실패 로그도 보존한다.

## 남는 검증 경계

이번 브라우저 시나리오는 화면·프로젝트 생성·탐색 검증이다. 실제 모델 학습, GPT 실계정 호출, 사용자 원본 자료 작업, 설치 패키지 교체를 실행하지 않았다. 전문가 라벨러 엔진 교체와 모델 정확도 개선은 별도 범위다. 처음 쓰는 사람의 완주율·소요시간·현장 품질은 실제 사용자 평가로 확인해야 한다.
