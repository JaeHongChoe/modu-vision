# 모두비전: 선택한 3번 디자인 구현 검증

검증일: 2026-11-15 KST. 브랜치: `refactor/novice-workspace-20261114`.
제품 기준: `6fefa39a203a1c5b51f49130d36caeaefaa9064c` (PR #30의 독립 출시 준비 작업 위에 쌓인 UI 변경).

사용자가 선택한 두 번째 시안 묶음의 3번을 기존 앱에 적용했다. 따뜻한 밝은 배경과 짙은 녹색, 사진 중심 작업 영역, 오른쪽 다음 행동 패널을 사용한다. 기존 여섯 단계는 네 가지 작업 목표로 묶어 표시하고, 모두 보기에서 여섯 단계를 직접 이동할 수 있다. 모델 10종, 저장·복구·취소·승인 경계와 편집기 수명은 유지한다.

![선택 시안과 실제 합성 QA 화면 비교](assets/novice-workspace/comparison.png)

## 변경 범위

| 영역 | 결과 |
| --- | --- |
| 공통 화면 | 중복 전역 안내를 정리하고 네 가지 작업 목표, 이름 있는 설정·도구 메뉴, 한 개의 다음 행동 패널을 구성했다. 언어/테마/프로젝트/계산 서버/용어 사전/도우미 접근을 유지한다. |
| 사진 준비 | 사진과 파일·클래스 표기를 중심에 두고 분할·클래스·데이터 관리 기능은 접어서 열도록 했다. 불러오기·경로 입력·오류·중요 경고는 접근 가능하다. |
| 라벨·학습·평가 | 라벨 캔버스, 선택 모델/실행 상태, 결과 이미지 확인을 우선한다. 상세 설정과 지표/비교/이력을 구분하되 실행 중 작업·복구·취소는 숨기지 않는다. |
| 흐름·검사 | 저장된 흐름과 실행을 중심에 두고 고급 그래프/단일 모델/패키지 도구를 이름 있는 보조 영역으로 정리했다. |
| 반응형 | 도우미가 차지하는 폭까지 고려해 오른쪽 패널을 아래로 옮긴다. 좁은 화면에서도 현재 작업과 권장 행동에 접근할 수 있다. |
| 기존 GUI 검사 | 50개 기존 spec의 새 메뉴·목표·접힌 영역 접근 경로를 옮겼다. 강제 클릭 없이 실제 메뉴를 열며, 요청/영수증/해시/복구/차단 단언은 유지했다. |

## 실제 검증 결과

| 검사 | 결과와 한계 |
| --- | --- |
| Renderer 전체 | `npm run test:renderer`: **3242개 중 3240 통과, 2 건너뜀, 실패 0**. 기존 스킵은 통과로 계산하지 않는다. |
| 최종 변경 집중 검사 | 공통 셸·데이터·도우미·준비도 관련 **380/380** 통과. 구현 에이전트의 별도 단계별 검사 결과와 구분한다. |
| TypeScript | main/renderer `npm run typecheck`, E2E `npm run typecheck:e2e` 통과. |
| 선택 디자인 브라우저 | 새 spec **1/1** 통과. 실제 격리 백엔드 + 합성 사진 64장, 여섯 단계, 각 단계 준비 상태 로드, 용어 사전/프로젝트 모달, 테마, 도우미, 좁은 화면, 경로 입력을 확인했다. 여섯 단계의 main 영역 axe serious/critical 0. |
| 기존 브라우저 회귀 | 접근성·초보자 경로·프로젝트 키보드·언어 검사 **7/7**, 단계 전환/취소/복구/작업 종류 및 언어 검사 **9/9** 통과. 언어 검사가 중복이므로 새 spec을 포함한 고유 브라우저 케이스는 **16개**다. |
| Electron 소스 실행 | 격리 HOME/프로필에서 **1/1** 통과. 1440×1024 창, 밝은 테마, 네 가지 목표, 다음 행동, 프로젝트 모달 및 Escape 확인. 설치된 앱은 교체하지 않았다. |
| 빌드 | 브라우저/Electron harness가 실제 renderer와 main 소스를 빌드해 실행했다. 배포용 동결 백엔드/설치 패키지를 만든 검사는 아니다. |
| 독립 검토 | 활성 학습 표시, 용어 사전 접근, 제목 스타일, 경로 입력 잘림, 의미 없는 옛 footer 단언을 수정 후 재검토. 미해결 P0–P2 없음. |
| 근거 무결성 | `check_service_plan.py` 통과. `check_action_evidence.py`: 207 actions, 1011 valid references, 438 pending, **0 accepted features**. 서비스 수락으로 확대하지 않는다. |

각 run의 로그는 `.superpowers/sdd/2026-11-14-novice-workspace-refactor/`에 보존한다. 최종 renderer는 `renderer-acceptance.log`, browser는 `browser-acceptance.log`, 기존 회귀는 `browser-regressions.log`와 `task4-final-navigation.log`, native는 `native-acceptance.log`다. 마지막 타입 검사는 `typecheck-acceptance.log`, `typecheck-e2e-corrected-acceptance.log`다.

앞선 실패도 보존했다. baseline 직접 node 명령은 저장소 TS preloader 누락으로 실패했고 올바른 명령으로 통과했다. 마지막 E2E 타입 검사에서 존재하지 않는 루트 `tsconfig.e2e.json`을 지정한 실행은 TS5058로 종료했으며, 저장소의 `npm run typecheck:e2e`로 바로잡아 통과했다. 466 px 갤러리 위치 검사 실패는 간격 수정 뒤 기존 450 px 상한으로 통과했다. 옛 GUI 영수증 해시 불일치는 과거 실행을 새 실행으로 바꾸지 않고 명시적인 역사 선언으로 해결했다.

## 화면 증거

- [사진 준비](assets/novice-workspace/implemented-data.png)
- [라벨](assets/novice-workspace/workspace-light-step-2.png), [학습](assets/novice-workspace/workspace-light-step-3.png), [평가](assets/novice-workspace/workspace-light-step-4.png)
- [흐름](assets/novice-workspace/workspace-light-step-5.png), [검사](assets/novice-workspace/workspace-light-step-6.png)
- [다크·도우미](assets/novice-workspace/workspace-dark-assistant.png), [좁은 화면](assets/novice-workspace/workspace-narrow.png), [전체 보기 경로 입력](assets/novice-workspace/workspace-full-path-fallback.png)
- [격리 native 창](assets/novice-workspace/native-selected-workspace.png)

이미지는 저장소의 합성 예제 자료다. 현장 데이터나 모델 품질 근거가 아니며, 시안의 사진 순서·문구를 흉내 내기 위해 실제 자료를 바꾸지 않았다. [디자인 QA](../../design-qa.md)에 비교 기준·수정 반복·의도적 차이를 기록했다.

## 역사 근거의 보존

`docs/service-action-evidence.json`에서는 실제 변경된 네 소스의 바인딩 해시만 갱신했다. `gui-test-source-history.json`의 선언은 152 → 184로 늘었으며, 32개 추가 선언은 기존 영수증의 테스트 바이트가 조상 `b42855d3676196e819395d19a649e183c4d4b594`에 존재함을 확인한 기록이다. 기존 한 선언의 현재 테스트 해시만 추가 갱신했다. 과거 영수증, 실행 소스 SHA, 원래 테스트 해시, 상태/승인은 보존했다. 이 선언은 변경된 모든 과거 GUI 검사를 다시 실행했다는 뜻이 아니다.

새 spec은 현재 fast selector의 `scripts/e2e/*.spec.ts` 수집과 full Playwright 검색에 자동 포함된다. 이 작업에서는 원격 full/fast CI를 새로 기동하거나 재실행하지 않았다. PR #30의 예전 CI나 성능 결과를 새 UI head의 결과로 재사용하지 않는다.

## 남아 있는 별도 검증

이 변경의 로컬 UI 검증은 완료했다. 새 head의 원격 CI, 설치 패키지 교체, 사용 중인 웹 서버 교체, 현장 데이터 품질 및 초보자 실제 사용성 측정은 별도다. 학습·평가·배포 전체 작업을 다시 수행하거나 실제 GPT 요청을 보내지 않았다. 원본 프로젝트, 라벨, 모델, 계정, 다른 작업트리와 기존 실행본은 변경하지 않았다.

## 2026-11-15 통합 회귀 후속

사용자가 현재 디자인의 CI 검증 후 main 병합과 설치 앱·localhost 웹 갱신을 요청했다. 선행 PR #30의 정확한 `6fefa39a` full `43600029940`을 새로 읽었으며 source는 성공, browser는 27개 실패였다. 이전 성공으로 처리하거나 실패 실행을 지우지 않는다. 현재 브랜치는 이 선행 통합을 포함하므로 최종 변경을 함께 exact-head CI로 검증한다.

- 공개 화면의 바뀐 버튼·접기·네 가지 목표/세부 단계에 맞춰 기존 검사를 갱신했다. 기존 요청 본문·소유권·원본 해시·과거 영수증·저장 임계값과 합성 PNG 픽셀·품질·거절 조건은 유지한다. 재시도, 강제 클릭, 제한 시간 확대는 추가하지 않았다.
- 실제 화면 결함 두 개를 수정했다. 헤더의 stacking context에 갇힌 프로젝트 창이 검토 큐 도구 모음 아래에 놓이던 문제를 해소했고, 간단 보기에서 데이터 재조회 중 안내가 접힌 관리 도구 안에 숨지 않도록 화면 위에 상태를 표시한다.
- 큐 14개 케이스는 첫 실행 11개 성공/3개 실패 뒤 헤더 수정 후 해당 3개 성공을 확인했다. 흐름·모델 지원 7개, OCR 3개, 비교 작업 1개, 저장 임계값 1개도 통과했다. 이는 여러 격리 실행의 고유 케이스 결과이며 단일 full CI 성공이 아니다. 첫 사용 경로도 새 버튼으로 합성 예제 생성·CPU 학습·평가·12장 검사·저장 이력·재열기까지 1개 성공(1.2분)을 확인했다. 12장 중 10장 이상 정답과 예제의 품질 승인 거절 조건을 유지했다. 선행 실패 27개 고유 케이스가 이처럼 최종 수정에서 통과했으며 현장 품질의 근거는 아니다.
- 최종 변경의 renderer/main 회귀는 3,242개 중 3,240개 성공·2개 skip·실패 0, 두 TypeScript 검사와 빌드, harness 70개를 통과했다. 밝은 테마 CSS는 정식 생성기로 갱신했다. 독립 diff 검토에서 미해결 P0–P2는 없었다.
- 후속 GUI 역사 선언은 184 → 194다. 기존 30개의 현재 테스트 해시를 갱신했고 10개는 조상 `1e54e49acb56686df5fda23ebb3937a3f219b9d5`의 정확한 옛 테스트 바이트로 연결했다. 기존 영수증·실행 소스·원본 테스트 해시·수락 상태는 보존한다. action source 바인딩은 실제 변경된 `WizardHeader.tsx`, `DatasetStudio.tsx` 두 곳만 갱신했다. 두 증빙 검사도 오류 0으로 통과했으며 207 actions, 1,011 valid references, 438 pending, accepted features/packages 0을 유지한다.

이 후속의 원본 로그와 단계 상태는 `.superpowers/sdd/2026-11-15-novice-workspace-release/`에 보존한다. 원격 CI·실제 main 병합·동결 패키지 자격·설치 앱/웹 갱신은 각각 실제 결과를 확인한 뒤 기록한다. 현재 이 문단은 그 완료를 주장하지 않는다. 기존 현장 품질·초보자 사용성·실제 계정 검증의 한계도 유지한다.
