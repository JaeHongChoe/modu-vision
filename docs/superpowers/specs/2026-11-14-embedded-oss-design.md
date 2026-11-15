# 모두비전 오픈소스 기능 내재화

## 목적과 범위
사용자는 모든 단계에서 유효한 오픈소스를 활용하되 별도 제품·사이트·서버를 쓰게 하지 않고 모두비전 안에 내재화하기를 요청했다. 기존 앱/localhost 화면·CLI·GPT·프로젝트·작업 원장을 유지한다. 기존 GPT 연결을 새 로컬 LLM으로 바꾸거나 원본 데이터를 외부 분석 서비스로 보내는 작업은 포함하지 않는다.

## 방식 비교와 선택
전체 외부 UI/서버를 얹는 방식은 사용자 목적과 다르고 저장·권한·패키지 부담을 늘린다. 모든 자체 코드를 라이브러리로 교체하는 방식도 검증한 작업/취소/정답/기록 의미를 잃는다. 선택은 (1) 작은 순수 알고리즘 선별 이식, (2) 내부 서비스 뒤의 버전 고정 라이브러리, (3) 이미 내재화된 엔진은 유지하고 구체적 결함만 개선하는 것이다. 이는 사용자 방향에 따른 구현자의 기술 선택이다.

## 단계별 산출물
| 단계 | 실제 반영 |
|---|---|
| 1 데이터 / 2 라벨 | cleanlab2.9의 normalized-margin 품질 순위를 순수 NumPy 어댑터로 내장하고 저장된 공통 시험 묶음의 분류 평가에서 라벨 재확인 순위를 만든다. 기존 검토 큐·원본 이미지 열기·재접속 사용. PR26의 polygon 편집은 별도 병렬 변경으로 보존한다. |
| 3 학습 | Optuna5.0 sampler를 기존 AutoDL ask/tell 후보 선택에 내장. 작은 기본 예산은 현재 탐색, 충분한 예산은 적응 탐색. 기존 실행/예산/정지/저장 원장 유지. |
| 4 평가 | 공식 COCO와 독립 대조한 검출 AP 계약v2. 미존재 GT 클래스와 두 번째 미매칭 GT 처리 결함을 새 버전에서 수정하고 과거 결과는 원래 정의로 유지. |
| 5 흐름 | 기존 캔버스의 키보드 이동·Escape·focus 이동·한국어 안내 강화. React Flow 전체를 새로 얹지 않음. |
| 6 검사 / 전체 진단 | 기존 내장 ONNX/OpenVINO 경로 유지. OpenTelemetry 로컬 SDK로 실제 API/도구 시간·성공·실패와 명시적 인과관계를 앱 안에서 확인. |

FiftyOne/MongoDB, MLflow 서버, LangGraph 실행기 교체, Kornia 전면 교체는 이번에 도입하지 않는다. 비용과 중복에 비해 현재 확인한 이득이 작기 때문이다. 이 판단은 영구 배제가 아닌 이번 적용 범위다.

## A. 내장 라벨 품질 순위
정확한 upstream2.9.0 source pin·Apache2 LICENSE/NOTICE·변경 표시를 유지하며 normalized_margin의 순수 수학 부분만 이식한다. full cleanlab/pandas/pyarrow/Datalab dependency를 사용자 프로세스에 넣지 않는다. 이는 cleanlab 전체 오류 탐지기가 아니라 명시한 순위 알고리즘 이식이다.

입력은 현재 프로젝트·source·labelset에 속하는 저장된 common-cohort classification 평가다. class_scores 전체 N×K와 고정된 class order, truth, source/truth/evaluation/checkpoint/training-version identities를 검증한다. top1 confidence로 확률을 복원하거나 모델을 다시 실행하지 않는다. 누락·NaN·범위·합·중복·class order 불일치와 ordinary/validation legacy 평가를 거절한다. N×K는2,000,000개 이하, N<=50,000/K<=1,000; score는 [0,1]이며 낮은 순, 동률은 안정적인 source/sample identity 순이다.

현재 common cohort는 train+val과 exact-pixel exclusion을 검증한다. 과거 학습 시점 LOT/group proof가 없으면 group_independence=unverified를 보존하고 결과 전체를 advisory_only로 표시한다. 참조 순위를 오라벨 확정·독립 품질 수락으로 쓰지 않는다. source/truth/revision/class/labelset가 바뀌면 분석은 stale이며 수정한 truth로 조용히 재계산/덮어쓰지 않는다.

분석은 evaluation_id 및 evidence SHA에 묶인 immutable JSON이다. 기존 data workbench review queue에 분석 ID·점수·given/predicted class·reason을 추가하고 기존 queue cursor/reviewer/return-to-evaluation을 재사용한다. 라벨 자동 수정/초안 채택/정답 확정과 진행 중 학습 변경은 없다. 분석은 선택 기능이며 자료 준비/학습의 새 필수 사람 대기를 만들지 않는다. 기존 SavedReviewQueuePanel에 한국어 라벨 재확인 동작·유효한 빈 상태·범위 변경 후 늦은 응답 거절을 둔다.

## B. AutoDL 내부 적응 탐색
search_strategy='auto'|'deterministic'|'adaptive'를 학습 설정과 구분해 같은 REST/CLI/recipe/remote 전달 계약에 둔다. auto는 search의 max_trials>=8이면 Optuna, 작은 예산은 기존 diagonal/Cartesian이며 quick/fast_retrain은 그대로다. adaptive 명시는 지원되는 유한 discrete domain만 사용한다. n_startup_trials=4,n_ei_candidates=24,multivariate=False,constant_liar=False 및 동일 seed를 기록한다. study.optimize/별도 scheduler/storage server는 없다.

검증한 discrete domain을 suggest_categorical로 제안하고, 중복은 실행 전 거른다. bounded retries 후 남은 기존 enumeration으로 fallback하며 full Cartesian을 메모리에 만들지 않는다. 실제로 검증을 마친 finite objective/latency/owned artifact만 COMPLETE feedback 및 winner 후보가 된다. 실패/취소/시간·메모리 종료는 기존 lease·원장 규칙대로 처리한다. 기존 최소화 objective 및 latency penalty를 그대로 전달한다.

전략·Optuna 버전·domain hash·sampler options/seed·proposal ordinal을 search.json/전달 config에 남긴다. 재개/재사용은 기존 seed/config/lineage에 전략 identity를 더해 분리하며 정확한 optimizer continuation으로 표현하지 않는다. 올바른 per-epoch heldout progress가 없는 모델군에는 pruning을 붙이지 않는다. 이번 산출물은 sampler이며 pruner는 capability에서 false/reason을 명시한다.

## C. 검출 AP 새 계약
공식 COCO의 동일 입력 제한에서 미존재 GT class는 macro 평균에서 제외하고, matching은 이미 사용한 noncrowd GT를 건너뛴 뒤 다음 qualifying unmatched GT를 찾는다. score 동률은 안정적이고 empty 결과는 명시한 unavailable/zero 규칙을 따른다. crowd/ignore/area/maxDet를 지원하지 않는 기존 simplified 입력을 COCO 전체 구현으로 부르지 않는다.

기존 evaluate_detections_map 기본 의미와 저장 결과를 몰래 바꾸지 않는다. 별도의 versioned evaluator(예: detection_ap_v2)를 두고 신규 평가 및 validator에 metric_contract를 저장·해석한다. 과거 미표기 결과는 legacy 계약 그대로 대조한다. UI가 버전 차이를 숨기지 않으며 이전 승인/품질 근거를 새 점수로 대체하지 않는다. 독립 pycocotools/TorchMetrics 고정 fixture 대조는 개발 검증용이며 사용자 runtime에 외부 평가 UI를 넣지 않는다.

## D. 로컬 trace
실제 OpenTelemetry SDK를 쓰며 로컬 bounded exporter만 둔다. JS API1.9.0 + sdk-trace2.12.0, Python sdk/api1.45.2/semantic-conventions0.66b2를 고정한다. collector/OTLP exporter/환경 기반 endpoint discovery/resource hostname 탐색/console exporter는 없다. span 이름·opaque IDs·허용 operation template·status/code·SDK start/end/duration·parent/link·coverage/drop count만 보존한다. URL/query/body/header/prompt/image/raw path/account identity/exception stack은 넣지 않는다.

현재 API request wrapper 안에 passive instrumentation을 넣어 성공·실패·JSON/context/cancel 경로를 측정한다. 새 ASGI auto-instrumentation middleware는 만들지 않는다. 기존 middleware identity/admission 계약을 유지하면서 선택적으로 explicit traceparent를 해석한다. 오류/반환 객체 identity를 계측이 바꾸면 안 된다.

renderer/backend scope별 ended span256개, export1MiB, attributes/events/문자열 상한, scope LRU/overflow count. source/account/project A-B-A 변경 후 이전 span은 현재 화면에 나타나지 않는다. 익명 global export는 없다. GET local trace 진단은 현재 인증 프로젝트/actor만 읽는다. trace 정보는 권한이나 ledger record를 대체하지 않는다.

초기 end-to-end causality는 명시적으로 trace handle을 넘기는 assistant read_app_command/jobs 진단에 연결한다. 나머지 API는 standalone root로 측정하고 unlinked임을 표시한다. 전역 mutable active tool로 background polling을 잘못 엮지 않는다. background queue/worker는 실제로 captured parent를 전달한 경로만 linked라고 하며, 과거 job ledger timestamps를 새 span으로 꾸미지 않는다. 성공 관측/시간이 있는 진단을 GPT와 사용자가 같은 현재 scope에서 읽을 수 있도록 기존 진단 표면을 확장한다.

## E. 기존 UI의 흐름 접근성
CustomNode와 FlowchartStudio/GraphNavigator를 확장해 선택 node의 방향키 이동·Shift 큰 이동, keyup/blur 기준 undo group, Escape 연결/선택 취소, 검색 결과 node focus 및 offscreen pan, 짧은 한국어 안내/live region을 제공한다. 자식 input/button 키를 훔치지 않고 busy/graphEditingDisabled에서 거절한다. node/edge ID·typed graph·semantic execution key와 저장 version 의미는 바꾸지 않는다.

DESIGN.md/UX-CONTRACT.md, workspace-button/dialog/native select/inline error/기존 review queue source of truth를 사용한다. 새 별도 dashboard·테마·toast 구현·AI 기술명 중심 화면을 만들지 않는다. 양쪽 테마·좁은 폭·focus·busy/error/empty/stale 상태를 검증한다.

## 배포/검증
새 라이브러리는 앱 build inventory/해시 lock/라이선스·source provenance에 포함한다. 실제 사용자에게 pip 설치나 별도 도구 실행을 요구하지 않는다. frozen source가 라이브러리 선택을 검사하고 missing/incompatible은 명시적으로 실패한다. remote environment가 지원하지 않는 adaptive 전략을 묵시적으로 다른 전략으로 실행하지 않는다.

모든 항목 TDD, 고정 합성 입력 및 fake/owned workers로 계약 검증, actual-library numerical comparison, 신규 API role/scope/revision 경계, renderer/main regression, type/build/generated types/service/action integrity, 별도 additive embedded CI와 독립 whole-branch review를 수행한다. 기존 ci.yml과 CI digest 보호는 유지한다. 첫 적용은 feature branch/PR이며 live 앱·웹·원본자료·계정·GPU·모델·다른 checkout/자동화는 바꾸지 않는다. benchmark/제조 품질/모든 OS/frozen 설치·외부 GPT 실제 호출은 실행한 근거만 별도로 기록한다.

## Builder-check rulings
- Probability evidence additionally checks stored top1/confidence against full matrix, not just row sums. Quality queue is a dedicated mode; correctly predicted low-margin rows are retained and ordered by score, not old error priority.
- A label edit during review marks the immutable analysis basis changed; historical queue progress may continue with per-item basis_changed copy. New queue creation from stale analysis is refused. Bytes/identity changes retain existing hard stale guards. No silent re-scoring or label approval.
- AutoDL domain validation and enumeration are separated; duplicate keys exclude ordinal seed. tell consumes the same signed metric+latency objective only after durable completed receipt.
- Actual metric entry point is evaluate_detections_map. Dispatch version at both ordinary route and common-cohort worker/validator. All-classes-no-GT is unavailable, distinct from GT-with-no-predictions=0.
- At least one actual owned assistant read→backend span→diagnostic readback must be proved; detached SDK tests alone are insufficient. Unconverted async jobs stay explicitly unlinked.
