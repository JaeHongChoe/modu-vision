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

### 첫 통합 CI 이후 보완

PR #31의 `49ebcec9` fast `43629673503`은 실패했다. CPU 6개 job과 browser 3/4는 성공했으나, 정적 job의 TS fixture 로더와 나머지 browser 3개 job에 옛 화면 접근 경로가 남아 있었다. security, embedded OSS, Studio CLI는 성공했다. full은 아직 시작하지 않았고, full 부재로 실패한 readiness gate도 재실행하지 않았다. 이 실패들을 최종 head의 성공으로 덮어 쓰지 않는다.

- 로더는 실제 `workspaceSettings.ts`를 읽도록 고쳤다. 기존 20개 테스트 본문은 유지했고 20개 실패 → 20개 성공을 확인했다.
- 컴퓨팅 서버 모달을 공개 닫기 버튼으로 닫은 뒤 헤더를 읽고, 테마·실행 대상은 실제 설정 메뉴를 열어 선택한다. 검사 화면의 새 제목과 독립된 도우미 열기 버튼으로 검사를 옮겼다. 진단 오류는 사진 영역의 alert로 범위를 명시해 다음 행동 패널과 중복되는 문구를 구분한다. 요청·권한·저장·원본·오류·시간 상한은 유지했다.
- 관련 브라우저 고유 14개 케이스가 여러 집중 실행에서 통과했다. 실제 Electron 검사 서비스 수명 검사는 첫 병렬 실행에서 본문 전 시작 시간 초과 뒤, 같은 상한의 직렬 실행으로 1개 통과했다. 추가 도우미 native 검사는 backend healthy 이후에도 `electron.launch` 60초 연결 시간 초과로 본문에 진입하지 못했다. 이 시작 실패의 원인을 확정하거나 native 도우미 검사를 통과했다고 주장하지 않는다. 소유 프로세스는 종료됐고 로그를 보존했다. 실제 GPT 요청은 없다.
- 현재 backend 소스 426개와 동결 파일의 바이트를 대조했다. 새 복사본의 첫 120초 diagnostics 실패를 보존하고, 별도 dyld 진단 관찰 후 동일 상한의 정식 diagnostics·시작·재시작 acceptance 성공을 확인했다. 성공 영수증은 새 복사본에만 붙였으며 cold-start 지연 원인이 해결됐다는 근거는 아니다.
- 첫 macOS 디렉터리 패키징은 `react-remove-scroll-bar@2.3.8` 라이선스 원문 누락으로 afterPack에서 실패했다. npm 배포본에 LICENSE가 없고 저장소에 이미 고정된 upstream 보충 원문이 있으므로, 수집기가 정확한 버전·lock integrity·immutable blob·SHA256·1093바이트를 확인해 기존 원문을 포함하도록 보완했다. 다른 누락과 변조는 계속 거절한다. 라이선스 및 release-readiness 검사 31개와 실제 49개 패키지 수집(누락 0)이 통과했다. 이 결과는 macOS 앱 설치나 공개 배포 승인 완료를 뜻하지 않는다.

원본 실패·합성 QA·새 검증 결과는 같은 SDD 폴더에 나란히 보존한다. 이 보완은 제품의 모델·계정 동작을 바꾸지 않으며 설치 앱과 기존 localhost 데이터는 아직 교체하지 않았다.

### 패키지 실행과 전체 회귀의 기하 검사

`e7bddab0` fast `43632585156`은 정적 1·CPU 6·browser 4개 job 모두 성공했고 보조 workflow 3개도 성공했다. 같은 커밋 full `43633690651`은 한 번 시작했다. browser job은 본 E2E 전 `Short-window labeling geometry`에서 실패했으며 원본 로그를 보존했다. 당시 측정은 모든 창 크기에서 canvas 248 px였다. 해당 fixture가 `index.css`의 새 `novice-workspace.css` import를 빠뜨린 것을 격리 재현했다. 실제 CSS를 적용하면 기존 캔버스·설정 축소·집중 편집 기준을 충족했다. fixture의 수동 CSS 목록과 옛 가로 폭 mock을 정식 import 그래프·실측 경계·실제 팝오버 계산으로 고쳤으며, 원래 단언 12줄과 수치·창 범위·시간 상한은 유지했다. 기하 검사 1개, 팝오버·테마 검사 11개 및 E2E 타입 검사를 통과했다. 이 실행에서 본 browser E2E는 미도달이며 full 성공으로 기록하지 않는다.

같은 `e7bddab0`에서 새 macOS 디렉터리 앱을 만들었다. 실제 ad-hoc 서명과 strict deep 검사를 통과했고 정식 read-only readiness 결과는 `runtime_ready`다. 별도 격리 HOME/userData에서 실제 packaged 앱·동결 backend의 경로·부모 PID·SHA256·인벤토리와 health를 확인했다. 합성 사진 64장, 여섯 단계 밝은 화면과 어두운 compact 화면, 정상 종료와 소유 PID 7개·포트 종료도 확인했다. 최초 확인 스크립트가 `/health`에 없는 `pid` 필드를 기대해 중단한 실패는 보존했고, 실제 공개 계약을 대조해 고친 별도 스크립트 실행으로 통과했다. 제품의 API·시간 상한은 바꾸지 않았다. 실제 계정·GPT·학습·추론 요청은 없었다.

이 앱 실행은 로컬 ad-hoc 후보의 자격이며 publisher 서명·공증·현장 품질·모든 OS의 공개 배포 수락을 뜻하지 않는다. 설치된 앱과 기존 웹의 실제 교체는 최종 커밋 CI 및 병합 이후 별도로 확인한다. 기존 실패·미검증과 이전 실행의 소스 신원도 보존한다.

본 E2E의 추가 집중 검사는 고유 8개 케이스가 통과했다. 큰 DAG와 팀 잠금은 원래대로 성공했고, 라벨 focus·갤러리/도우미 공통 오류 3개·실행 예산은 오류가 본문과 다음 행동 패널에 함께 표시되어 생긴 strict 중복을 원래 오류 소유 영역으로 한정한 후 성공했다. 예산 검사에서 학습 제출은 0회였고 실제 CPU 학습이 있는 별도 case는 이번 집중 검사에서 선택하지 않았다. S1-10 plain browser는 옛 폴더 대화상자 오류 대신 현재 공개 버튼이 여는 서버 경로 입력, 브라우저 제한 안내, 빈 경로 제출 비활성 및 프로젝트 native browse 비활성/title을 확인하도록 이관했다. 원래 240초 실패와 trace는 보존하며 제한을 늘리지 않았다. 두 chooser의 역사 선언은 기존 194개 중 현재 테스트 해시 두 곳과 이유만 갱신했고, 원본 영수증·실행 SHA·원래 테스트 해시·수락 상태는 보존했다. 실제 모델을 필요로 하는 별도 DINO case는 실행하지 않았다. 이 후속 변경은 fixture·검사·기록에 한정되며 이미 실행 검증한 앱의 제품 코드·백엔드는 바꾸지 않는다.

### 785d7f7e 전체 브라우저 회귀의 후속

정확한 `785d7f7e` fast는 11개 job과 보조 workflow 3개 모두 성공했다. full `43636650817`은 source 성공, browser 359개 성공·15개 실패·2개 skip으로 종료했다. 기하 검사는 성공했고 본 E2E가 실행된 결과다. 원본 job 로그 784,934바이트(SHA256 `5889202c6818293d30c9c81a17d1f443bc3e8f7ffdd1e364a42b63f6c32e1b68`)를 0600으로 보존했다. 이 head의 실패를 지우거나 성공으로 재해석하지 않는다.

15개는 현재 공개 UI와 맞지 않는 검사 접근 경로·fixture 가정이었다. 실제 source 경로는 가져오기 도구의 visible 표시로 한정했고, 평가 3개는 summary의 직접 소유 details를 열어 ROC 산술·화소·원본 오버레이·provenance·거절 조건을 끝까지 검사했다. 인증된 흐름 fixture는 현재 브라우저 폴더 경로 버튼명을 사용한다. 흐름 드래그는 공개 전체 맞춤 후 노드 헤더의 실제 hit를 확인한 위치에서 원래 거리와 6단계 pointer 동작으로 수행한다. 기존 cache·A/B/A·freeze·503 복구·10초 준비 기한·되돌리기·저장 중 이동 거부도 유지했다. 옛 `appFlow.ts` 고정 해시 2건은 당시 단일 메뉴 경로 변경을 읽기 검토한 뒤 정확한 현재 해시로 갱신했다. 패키지는 공개 보조 disclosure를 열고, specialist runtime은 현재 데이터 준비 버튼을 기다린다. 실제 합성 CPU runtime 검사는 원래 1.2초 예산에서 `time_limit`으로 끝나며 batch 처리, 원본 SHA, 모델/영수증 미생성과 등록 모델 0을 확인했다.

집중 실행은 실패했던 15개와 드래그 helper의 이동 거부 1개를 포함해 고유 16개가 성공했다(평가/derived 4, 흐름 8, pin·cohort·runtime 4). 초기 local 접근 수정 실수와 원래 RED도 별도로 보존했다. 제품·모델·보호 조건·원래 산술/픽셀/시간 단언은 바꾸지 않았다. 역사 선언은 194 → 205로, 기존 5개의 현재 hash/reason만 갱신하고 11개는 정확한 조상 `785d7f7e6cd992a087ed82ee704830886192bff1`의 원래 검사 바이트와 연결했다. 기존 anchor·영수증·실행 SHA·원래 검사 hash·수락 상태를 유지하며 신규 native 실행이나 사람 수락으로 계산하지 않는다. 최종 새 head의 원격 회귀와 설치 앱·웹 적용은 여전히 별도다.

마지막 보완 후 E2E 타입 검사, harness 70개, 서비스·행동 증빙 검사와 diff 검사를 통과했다. 증빙 수치는 207 actions·1,011 valid references·438 pending·accepted features/packages 0을 유지한다.
