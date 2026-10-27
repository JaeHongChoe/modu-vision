# 작업을 수행하는 내장 도우미 — 조사와 설계 방향

2026-10-27. 사용자는 조사 결과와 추천한 작업형 도우미 방향에 “ㅇㅇ”로 구현 진행을 확인했다. 아래는 그 방향의 구현 계약이다. 코드·CI·실제 사용자 수락은 각각 검증한다.

## 사용자 목표

사용자는 질문과 작업 요청을 자연어로 말하고, 도우미가 실제 앱 화면을 조작해 그 작업을 수행하는 경험을 원한다. Antigravity IDE에서 에이전트가 사이트를 조작하는 방식이 참고 대상이다. ML 용어와 메뉴를 먼저 배워야 하는 접근을 줄이는 것이 목적이다. 실행 계획을 한 번 확인한 뒤 일반 작업을 이어가고, 라벨 검토·정답 확정·최종 승인에서는 사람이 판단하는 방향을 사용자가 확인했다.

성공의 예는 “이 폴더로 균열 검사 만들어줘” → 꼭 필요한 질문 → 프로젝트·자료 준비 → 라벨 초안과 검토 → 학습·평가 → 검사 흐름 준비 → 결과 확인이다. 사용자가 매번 다음 메뉴와 버튼을 찾아야 하면 이 목표를 충족한 것이 아니다.

## 공식 자료에서 확인한 Antigravity의 방식

- IDE 에이전트가 편집기·터미널·브라우저를 이용해 작업을 계획하고 실행·검증한다. 이를 이 앱에 적용할 때의 핵심은 대화 응답 이후 실제 작업을 계속하는 실행 루프다. [IDE 개요](https://www.antigravity.google/docs/ide/overview/), [Google 발표](https://developers.googleblog.com/en/build-with-google-antigravity-our-new-agentic-development-platform/).
- IDE의 Browser Subagent는 로컬 Chrome을 열고 읽고 조작하며, 화면 캡처와 작업 영상을 결과물로 남긴다. [IDE 브라우저](https://www.antigravity.google/docs/ide/browser).
- 작업 계획을 검토하고 피드백을 반영할 수 있으며, 완료 후 walkthrough에는 변경 내용과 브라우저 화면·영상 근거가 들어간다. [계획](https://www.antigravity.google/docs/ide-implementation-plan), [완료 보고](https://www.antigravity.google/docs/walkthrough).
- App/CLI의 `/browser` 명령 설명을 IDE의 인터페이스와 혼동하지 않는다. 이번 비교의 기준은 IDE 문서다.

위 기능을 그대로 이식할 수 있다는 뜻은 아니다. 다음 구성은 이 앱의 소유 코드와 기존 기능을 보고 제안하는 설계다.

## 현재 구현과의 차이

- 내장 도우미에는 실제 함수 호출 루프와 상태/이동 도구, 분할 저장·분류 클래스 지정·초안 생성·학습 시작의 확인 후 실행 경로가 있다. 단순히 대화 API만 붙인 상태는 아니다.
- 현재 상태 입력은 하단 안내·단계·수치·일부 오류의 요약이다. 기능 설명을 검색하고 현재 화면의 가능한 조작을 상세히 알아내는 능력이 부족하다.
- 질문마다 문맥을 고정하고 모델 호출을 최대 6회로 제한한다. 장시간 작업과 재접속 후 이어하기의 작업 원장이 별도로 필요하다.
- `AutoProgressDialog`와 `autoProgress`에는 이미 분할 → 학습 → 모델 평가 → 흐름 저장/평가 → 시험 검사까지 가는 별도 실행 체인이 있다. 이 기능은 대화형 도우미의 실행·확인·기록 구조와 분리되어 있다.
- 확정 라벨·정답·승인과 모델 초안을 분리하는 기존 가드는 유지해야 한다. 작업 완료와 모델 품질 수락도 같은 상태가 아니다.

## 검토한 두 구성

| 구성 | 사용자가 보는 경험 | 구현상 장점과 비용 |
| --- | --- | --- |
| 화면 클릭 중심 | 에이전트가 버튼·입력을 직접 찾아 조작 | 화면 구조와 위치 변경에 따른 재시도·오작동 대책이 많이 필요하고, 웹과 데스크톱의 파일 창 차이를 따로 처리해야 함 |
| 앱 기능 명령 + 화면 동기화 + 화면 검사 (추천) | 도우미가 해당 화면을 열고 입력·실행하며 현재 행동과 결과를 보여 줌 | 기존 버튼과 같은 저장·학습·평가 경로 및 권한 검사를 재사용하고, 필요할 때 의미 있는 화면 상태를 읽어 다음 행동을 결정 |

추천 구성은 도우미가 작업을 직접 수행하도록 한다. 각 버튼·폼에 안정적인 행동 ID와 스키마를 연결하고, 그 명령이 화면 상태와 실제 저장 결과를 함께 갱신하게 한다. 단순 좌표 클릭이나 도우미에게 임의 JavaScript/터미널 실행권을 주는 방식은 이 앱의 기본 실행 수단으로 삼지 않는다.

## 작업 루프와 화면

자연어 요청 → 기능/화면/프로젝트 상태 확인 → 필요한 질문 → 실행 계획 → 사람 확인 → 행동 → 상태·저장 결과 재확인 → 다음 행동 또는 사람 검토 → 완료 결과물.

- 대화 패널에 실행할 작업, 현재 처리 단계, 완료·대기·실패와 다음 행동을 보인다. 관련 화면도 실제로 따라 이동하고 대상 폼·버튼이 무엇인지 표시한다.
- 실행 중 “잠깐, 설정을 바꿀게” 같은 사용자 개입을 받아 정지·변경·재계획할 수 있어야 한다.
- 완료는 호출을 보냈다는 표시로 판단하지 않는다. 프로젝트 ID, 저장 버전, 작업 ID, 결과와 실제 화면을 다시 읽어 확인한다.
- 작업 종료 때 무엇을 만들었고 무엇이 남았는지, 확인할 결과 화면과 저장된 산출물을 간단히 보여 준다.

## 필요한 실행 계층

1. **기능·화면 지식.** 앱 버전과 지원 기능, 현재 화면의 입력/행동/전제 조건, 사용 안내를 조회하는 도구. 모델이 없는 메뉴를 지어내거나 사용자에게 버튼 이름을 다시 물을 일을 줄인다.
2. **공통 행동 등록부.** 현재 프로젝트/자료 가져오기, 라벨 초안, 학습 설정/실행, 평가, 흐름 편집/저장/평가, 시험 실행, 보고서 만들기를 기존 서비스와 같은 경로로 연결한다. 웹·앱에서 같은 역할·준비도 정책을 쓴다.
3. **작업 실행기.** 질문 1회의 대화 루프와 별도로 계획·작업 상태를 보존한다. 단계 사이마다 최신 상태를 읽고, 모델이 제안한 행동의 전제와 성공 조건을 앱이 검사한다. 기존 자동 진행도 이 실행기를 사용하도록 합친다.
4. **작업 단위 확인.** 승인된 계획에는 허용할 행동·대상 자료·자원과 변경 범위를 담는다. 이 범위의 일반 작업은 매번 확인을 반복하지 않는다. 범위가 바뀌면 변경분을 보여 준다. 모델이 자신에게 승인 권한을 부여할 수는 없다.
5. **재접속·오류 복구.** 작업 ID와 저장 버전을 기록하고 화면 새로고침/재로그인 뒤 결과부터 읽는다. 응답이 끊겼어도 학습·평가가 이미 완료됐으면 재실행하지 않는다. 방금의 Step5 대용량 JSON 사례가 대표 검증 대상이다.

작업 상태 초안: 계획 중 / 실행 확인 / 진행 중 / 작업 완료 대기 / 사람 검토 대기 / 일시정지 / 실패 / 완료. 프로젝트·계정·자료 버전이 달라지면 이전 대상에 쓰지 않도록 최신 문맥을 다시 대조한다.

## 사람의 판단과 데이터 범위

- 라벨 초안 만들기와 검토 화면 준비는 도우미가 한다. 초안을 정답으로 채택하는 행위와 정답 확정은 사람이 한다.
- 학습·평가·흐름 구성은 승인된 계획 안에서 실행한다. 현재 작업을 조사하면서 다른 프로젝트·작업·GPU를 중단하지 않는다.
- 모델·흐름의 최종 승인과 운영 배포는 별도 사람 판단으로 유지한다. 단순 시험 실행과 운영 배포를 구분한다.
- 기본 관측은 앱 내부의 구조화된 화면 상태와 필요한 수치/오류다. 이번 목표 확인을 실이미지·화면 캡처의 외부 전송 동의로 확대하지 않는다. 현재 ChatGPT 연결의 텍스트 범위를 바꿀 필요가 있다면 별도로 설계한다.
- 사용자에게 보이는 계획은 쉬운 작업 이름과 영향으로 설명한다. 내부 권한 ID·해시·프로토콜 상세는 일반 사용 흐름에 노출하지 않는다.

## 기능별 우선 순서

1. 현재 오류 원인·기능 설명·가능한 다음 행동을 실제 자료로 읽게 한다.
2. 프로젝트와 자료 준비, 화면 이동·입력·초안 준비를 연결한다.
3. 학습 → 평가 → 흐름 저장/평가 → 시험 실행의 기존 자동 진행을 대화 작업으로 묶는다.
4. 작업 원장, 중단·재개, 수동 개입과 결과 확인을 완성한다.
5. 앱/localhost 웹에서 같은 초보자 시나리오로 실제 화면과 저장 산출물을 검증한다.

## 검증에서 확인할 것

- 처음 쓰는 사람이 자연어 요청과 필요한 선택만으로 작업을 진행할 수 있는가. 질문·클릭·입력 수와 소요 시간을 측정한다. 기존 30분/40회 목표는 실측 전 달성으로 쓰지 않는다.
- 앱과 localhost 웹에서 화면 이동, 실제 입력값, 저장 결과와 작업 로그가 일치하는가.
- 도중에 사용자가 설정을 바꾸거나 프로젝트 A→B→A로 전환해도 예전 대상에 작업하지 않는가.
- 완료 응답을 잃은 뒤 재개해도 학습/평가/파일 생성이 중복되지 않는가.
- 잘못된 라벨, 정답 미확정, 품질 부족, 권한 거절을 정확히 멈추고 설명하는가.
- 합성/모의 공급자, 실제 앱 조작, 실제 모델 실행, 현장 품질과 사람 수락을 구분하는가.

## 별도 코드 검토 결과와 세부 설계에서 해결할 점

별도 에이전트가 현재 코드를 읽기 전용으로 대조했다. 대화 실행기와 자동 진행기를 공통 액션으로 합치는 방향을 확인했으며, 아래 빈틈을 추가로 확인했다. 이 검토는 구현·실행·초보자 수락 검사가 아니다.

- 프로젝트 생성/가져오기는 기존 저장소 함수와 선택 권한을 재사용할 수 있지만 현재 GPT 도구에는 없다. 도우미 자신이 계획대로 프로젝트를 만드는 전환과 사용자가 다른 프로젝트로 옮기는 전환을 구분해야 한다 (`useProjectStore.ts:579`, `useAssistantStore.ts:247`).
- 첫 프로젝트의 초안에는 완료 모델이 없을 수 있다. 기존 완료 모델 초안 도구만 연결하면 초보자가 멈춘다. 기존 문구 기반 Grounding DINO/SAM2 초안 경로를 함께 다루되 채택은 사람에게 남겨야 한다 (`assistantTools.ts:87`, `PhraseDraftCreate.tsx:16`).
- 현재 GPT 학습 경로와 화면/자동 진행의 커스텀·이어 학습 설정 범위가 다르다. 계획에 표시한 설정과 실제 실행 설정을 같은 영수증으로 대조해야 한다 (`assistantRuntime.ts:149`, `TrainingController.tsx:298`).
- GPT는 현재 모델 평가 결과를 읽지만 평가 실행 도구는 없다. 단일 모델 흐름 저장·전체 흐름 평가도 별도 자동 진행에 있다 (`assistantTools.ts:121`, `AutoProgressDialog.tsx:54`).
- 현재 자동 진행은 화면 문구 문자열로 행동을 고른다. 공통 실행기에서는 안정적인 행동 ID와 전제조건을 사용하고 한국어 표시 문구는 사용자 설명으로 유지한다 (`autoProgress.ts:33`).
- 실행 ID 헤더는 감사 표시이며 권한 허가가 아니다. 계획에 대한 실행 동의는 별도로 검증해야 한다 (`src/main/assistant/runHeader.ts:1`).
- 기존 HTML/JSON 보고서는 재사용 가능하지만 선택 코호트 보고서는 현재 미지원이다. 다른 코호트 결과를 보고서로 내보내서 완료로 처리하지 않는다 (`useEvaluationStore.ts:554`).
- 기존 E2E의 모의 공급자·합성 이미지 검사는 실제 GPT의 자연어 작업 능력과 초보자 사용성 검증을 대신하지 않는다 (`assistant-easy-use-supplemental.spec.ts:11`).

`assistantSession.ts:44,125,154,160`의 문맥 고정과 6회 제한은 짧은 모델 대화의 경계로 남기고, 지속 작업 감독기가 액션 사이마다 상태를 다시 읽도록 한다. 단순히 호출 제한만 늘리거나 GPT가 기존 자동 진행의 시작 함수를 호출하게 하는 방식으로 작업 범위 확인을 우회하지 않는다.

## 개발 범위와 보존

PR18/Step5 수정 ca52ac7a에서 파생한 별도 작업 공간에서 구현한다. 기존 설치 앱·실프로젝트·8443·기존 QA를 바꾸지 않는다. 기존 ChatGPT/SIWC 어댑터를 사용하며 API 키·새 공급자·호스팅 샌드박스는 도입하지 않는다. 실제 공급자 호출과 제조 이미지 외부 전송은 이번 자동 검사에 포함하지 않는다. 자동화는 재시작하지 않는다.


## 구현 인터페이스 계약

### 공통 DTO

`src/renderer/services/assistantTasks/types.ts`와 backend의 JSON 계약을 맞춘다.

- TaskStatus: draft, ready, running, waiting_human, waiting_job, paused, needs_reconciliation, failed, completed.
- StepStatus: pending, running, waiting_human, waiting_job, completed, failed, uncertain.
- ActionId: create_project, import_dataset, save_split, configure_training, generate_label_drafts, start_training, evaluate_model, save_flow, evaluate_flow, inspect_test, export_report, open_step.
- TaskPlanInput: `{goal:string, binding:string, steps:Array<{action:ActionId,args:Record<string,unknown>,label:string}>}`. binding은 로컬 실행기가 생성하며 GPT 인자로 받지 않는다.
- AssistantTask: `{schema_version:1,id:string,revision:number,status:TaskStatus,goal:string,plan_sha256:string,binding:string,steps:TaskStep[],approval:{plan_sha256:string,binding:string,confirmed_at:string}|null,current_step_id:string|null,message:string|null,created_at:string,updated_at:string}`.
- TaskStep: `{id:string,action:ActionId,args:Record<string,unknown>,label:string,status:StepStatus,attempt_id:string|null,receipt:Record<string,unknown>|null,error:string|null}`.
- TaskEvent: `{expected_revision:number,binding:string,type:'approve'|'begin_step'|'complete_step'|'wait_step'|'pause'|'resume'|'fail_step'|'reconcile_step'|'record_evidence',step_id?:string,attempt_id?:string,receipt?:Record<string,unknown>,message?:string,binding_after?:string,wait_kind?:'human'|'job',confirmed?:boolean}`.

API: GET/POST `/api/assistant-tasks`, GET `/{task_id}`, POST `/{task_id}/events`; 응답 `{task:AssistantTask}`, 목록 `{tasks:AssistantTask[]}`. 승인/계획 수정/실행 의도는 revision으로 경쟁을 거절한다. 실행 의도를 저장한 뒤 실제 앱 액션을 호출한다. 실행 중 응답 유실은 기록을 확인하기 전 자동 재실행하지 않는다.

이 API는 계정/CSRF/desktop capability를 유지하면서 프로젝트 생성 전에도 자기 actor의 계획을 보관할 수 있어야 한다. 다른 actor의 작업과 다른 등록 위치의 같은 논리 project ID를 섞지 않는다. 프로젝트/원본 변경을 허용하는 계획 단계의 성공 결과만 예상된 전환으로 취급한다. 실행 ID는 감사 표시이지 실행 동의가 아니다.

### 실행기와 앱 어댑터

`runner.ts`: `AssistantTaskRunner`와 `TaskRunnerDeps`를 노출한다. deps는 `read(id)`, `event(id,event)`, `binding()`, `execute(step,control)`, 선택적 `reconcile(step,control)`, `onChange(task)`다. `run(task)`와 `pause()`가 공개 메서드다. control은 `isCurrent():boolean`, `isPaused():boolean`, 연결 권한용 `isAuthorityCurrent?():boolean`, 실행 근거 저장용 `checkpoint?(receipt):Promise<void>`를 제공한다. 미실행 재개는 서버의 원자적 claim이 승인된 경우에만 실행한다. claim 응답 소실·경쟁은 자동 재시도하지 않는다.

`runtime.ts`: `taskActionCatalog()`(사용 안내와 행동별 입력 스키마), `taskRuntimeBinding():string`, `executeTaskAction(step,control):Promise<TaskActionResult>`, `reconcileTaskAction(step,control):Promise<TaskActionResult>`, `inspectTaskScreen():Record<string,unknown>`를 노출한다. TaskActionResult는 `{kind:'completed'|'waiting_human'|'waiting_job'|'needs_reconciliation',receipt?:Record<string,unknown>,message?:string,bindingAfter?:string}`다. role/준비도/입력 검사를 우회하지 않는다. 실제 실행 설정과 결과 식별자를 receipt로 남기고 모델·이미지 배열 전체를 응답에 넣지 않는다.

공통 앱 동작은 `services/assistantTasks/appActions.ts`에 추출하고 기존 자동 진행과 새 task runtime이 같이 쓴다. UI props와 전체 앱 저장소를 pure runner에 import하지 않는다.

GPT 도구는 `read_app_features`, `inspect_app_screen`, `read_task_status`, `propose_task`를 추가한다. propose_task는 계획을 제시할 뿐 실행을 승인하거나 실행하지 않는다. 사용자가 패널에서 확인하면 로컬 controller가 runner를 시작한다. 기존 단일 쓰기 확인 흐름도 유지하며 활성 task와 충돌하는 변경은 일시정지/거절한다.
