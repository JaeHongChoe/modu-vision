# 사용자 화면과 5단계 플로우 실행 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. 이 문서는 검토용이며 새로운 구현 완료 상태가 아니다.

**Goal:** 처음 사용하는 사람도 검사 플로우를 만들고 오류 원본을 확인할 수 있게 한다.

**Architecture:** Windows local-first 모듈형 API/worker/runtime 계약을 확장한다. 기존 프로젝트와 기능을 유지하며 영속 작업과 revision 검증을 공통으로 사용한다.

**Tech Stack:** React/TypeScript/Electron, Python/FastAPI/PyTorch, SQLite 및 versioned storage adapter.

**Spec:** ../specs/2026-10-02-windows-open-source-service-design.md

## 공통 조건

- Windows11 x64 우선, Linux GPU worker, 개인 모드와 선택 팀 서버. 지원표에 없는 조합은 preflight에서 차단한다.
- DINOv3 CLS/SEG/Patch와 YOLO detection 기본을 유지한다. 새로운 pretrained/OBB adapter와 distribution license는 출처·조건 검토 후 적용한다.
- 원본과 타 작업·프로세스를 보존한다. private source data와 비밀정보를 소스·예제·검증 로그·공개 artifact에 포함하지 않는다.
- 각 파일/함수 이름은 계획상 신규 계약이다. existing API compatibility를 확인하고 필요한 adapter와 migration을 같은 task에서 검증한다.
- 새로운 implementation/native/quality/signing/physical acceptance를 이전 test counts로 대체하지 않는다.

## 검토에서 빠지기 쉬운 입력

- 한글/공백/긴 Windows 경로, read-only 또는 locked file: 원본 보존과 구체적인 복구 행동.
- 작업/transfer/update/approval 중 접속 종료와 다시 시작: 동일 ID·revision·owner의 상태를 복구.
- 빈 정상 annotation과 UNKNOWN truth: 정상 판정이나 품질 pass로 자동 변환하지 않음.
- 두 사용자 또는 두 attempt의 동시 변경: revision/fencing/권한 확인으로 덮어쓰기와 중복 결과 방지.
- UI 선택 버전과 실행된 artifact가 다름: 실제 실행 identity·stale 사유·다음 행동 표시.

## task 실행 방법

각 task는 아래 acceptance를 먼저 실패 테스트로 고정한 후 구현한다. 신규 test 파일은 제안 위치이며 기존 테스트와 중복되는 경우 해당 production 경로의 회귀를 확장한다. UI smoke는 실제 Windows/Electron 또는 host adapter가 같은 renderer의 browser E2E로 확인한다. 실제 Windows native 증거는 browser 테스트와 구분한다.

## S2-01 첫 실행과 GPU 없는 예제

**Files:**

- Create: `src/renderer/components/onboarding/FirstRunStudio.tsx`
- Create: `examples/demo-project`
- Modify: `backend/engine/project_archive.py`
- Test: `scripts/e2e/service-s2-01.spec.ts`

**Interfaces:** `create_demo_project() -> DemoProjectRef`

**Consumes:** S1-05, S1-09

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 네트워크·GPU·외부 계정 없이 작은 합법적 예제의 실제 CPU 추론과 저장 flow를 실행한다.
- 개인 모드/팀 연결/서버 preflight/프로젝트 생성/데이터 가져오기 순서를 안내한다. 예제 결과와 실제 공정 품질 승인을 혼동하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s2-01.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s2-01.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S2-02 공통 디자인 시스템

**Files:**

- Extend directory: `src/renderer/components/common`
- Create: `src/renderer/styles/design-tokens.css`
- Create: `src/renderer/components/layout`
- Test: `scripts/e2e/service-s2-02.spec.ts`

**Interfaces:** `DesignTokens / FormField / AsyncAction / StatusBadge / WorkspaceDialog`

**Consumes:** 현재 baseline source와 위 설계의 공통 계약

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 색·글자·간격·버튼·폼·loading/empty/error/disabled를 통일하고 오류 원인과 해결 동작을 함께 표시한다.
- Windows 100/125/150/200% DPI, 1366×768·1920×1080, 키보드 focus와 색 이외 상태 표현을 확인한다.
- 노드 추가/선택/연결/삭제/undo를 키보드로 수행하고 dialog 종료 후 focus를 복원한다. DPI 변화 후 canvas 및 ROI drag 좌표가 원본 좌표와 일치한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s2-02.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s2-02.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S2-03 6단계 진행과 준비도 안내

**Files:**

- Create: `src/renderer/components/wizard/WizardFooter.tsx`
- Modify: `src/renderer/stores/useProjectStore.ts`
- Modify: `src/renderer/components/common/WorkflowImpactPanel.tsx`
- Test: `scripts/e2e/service-s2-03.spec.ts`

**Interfaces:** `get_next_action(ProjectContext) -> ReadinessAction`

**Consumes:** S1-01, S0-05

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 각 단계에 완료된 입력·부족한 항목·다음 행동 하나를 표시한다. 미준비 학습/승인/배포 행동은 이유와 해결 링크를 제공한다.
- 이전 결과의 조회와 전문가 이동을 일괄 차단하지 않는다. task 변경은 데이터 영향 미리보기와 원자적 적용을 사용한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s2-03.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s2-03.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S2-04 5단계 편집 화면 재구성

**Files:**

- Modify: `src/renderer/components/flowchart/FlowchartStudio.tsx`
- Create: `src/renderer/components/flowchart/FlowEditorWorkspace.tsx`
- Test: `scripts/e2e/service-s2-04.spec.ts`

**Interfaces:** `FlowWorkspaceTab = edit | test | evaluate | release`

**Consumes:** S2-02, S2-03

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 편집/테스트/일괄 평가/배포 4영역에서 주 행동을 하나씩 제공한다. 모델 선택기·좌측 palette·중앙 canvas·선택 노드 설정·이미지 viewer를 연결한다.
- draft/saved/evaluated/approved/deployed를 구분하고 저장되지 않은 변경·현재 활성 버전·실제 검사 버전을 항상 표시한다.
- 목적 안내 recipe는 single-model·검출ROI·고정ROI·회전→OCR/검사·전처리→다중모델→집계에서 시작한다. 호환된 완료 모델과 class mapping을 명시적으로 선택하며 기존 DAG를 자동 교체하지 않는다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s2-04.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s2-04.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S2-05 typed DAG와 검사 규칙 편집

**Files:**

- Modify: `backend/engine/flowchart_engine.py`
- Modify: `backend/engine/flow_operators.py`
- Modify: `src/renderer/components/flowchart/FlowGeometryEditors.tsx`
- Modify: `src/renderer/components/flowchart/CustomNode.tsx`
- Test: `scripts/e2e/service-s2-05.spec.ts`

**Interfaces:** `NodePort(type, cardinality, coordinate_space, score_spec) / validate_edge(...)`

**Consumes:** S0-02, S1-05, S2-04

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 고정 ROI·검출 ROI·patch·회전/정렬·Blob 면적/개수·클래스 분기·여러 결과 merge·다단계 모델 체인을 모든 지원 task에 맞게 연결한다.
- 연결 즉시 타입·클래스·좌표·범위 오류를 표시한다. cycle·빈 ROI·경계 밖 ROI·여러 crop aggregation·UNKNOWN은 명시적 규칙으로 처리한다.
- 영역 평균 밝기·size/probability filter·자유 경로/곡선 길이·검출 영역 면적·누락 object 규칙을 유지한다. mm/mm²는 검증된 pixel calibration/변환 메타데이터가 있을 때만 표시하고 px와 구분한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s2-05.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s2-05.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S2-06 공통 원본과 판정 viewer

**Files:**

- Create: `src/renderer/components/common/EvidenceImageViewer.tsx`
- Modify: `src/renderer/components/flowchart/FlowNodeDebugger.tsx`
- Modify: `src/renderer/components/inference/ReviewQueuePanel.tsx`
- Test: `scripts/e2e/service-s2-06.spec.ts`

**Interfaces:** `open_evidence(ImageRef, RunRef, NodeRef?, return_route) -> ViewerState`

**Consumes:** S1-06, S2-02

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 원본·overlay·heatmap·mask·bbox·ROI·중간 이미지·점수·판정 근거를 같은 viewer에서 비교하고 원래 작업 화면으로 돌아간다.
- 미검·과검·A/B 불일치·검수 queue에서 해당 원본과 정확한 실행 버전을 연다. 줌·pan·전체맞춤·overlay/라벨/opacity·지우개 등 버튼별 실제 UI 검증을 기록한다.
- historical run과 prediction snapshot은 read-only다. 라벨 편집은 명시적 labeling mode·current labelset·lease/revision 확인을 거친다. 외부/다른 dataset 이미지는 view-only다. 복귀는 report/version/image/node/filter/page 선택을 복원한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s2-06.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s2-06.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S2-07 대용량 이미지 선택과 검색

**Files:**

- Modify: `src/renderer/components/common/ImagePickerModal.tsx`
- Modify: `src/renderer/components/flowchart/FlowWorkspacePanel.tsx`
- Modify: `backend/api/routes_dataset.py`
- Test: `scripts/e2e/service-s2-07.spec.ts`

**Interfaces:** `query_images(cursor, query, filters) -> ImagePage / resolve_image_ids(ids)`

**Consumes:** S1-06, S3-01

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 200/64개 밖 이미지와 기존 저장 선택을 ID로 유지한다. 검색·태그·Lot·상태·오류·제품 필터와 가상화 목록을 제공한다.
- 페이지 변경·재시작·원본 이동에서 선택 누락을 표시하며 경로 기반 오선택과 전체 목록 eager 로딩을 방지한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s2-07.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s2-07.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S2-08 디버거와 A/B 및 버전 이력

**Files:**

- Modify: `src/renderer/components/flowchart/FlowWorkspacePanel.tsx`
- Modify: `src/renderer/components/flowchart/FlowDraftControls.tsx`
- Modify: `src/renderer/stores/useFlowchartStore.ts`
- Modify: `backend/engine/flow_workspace.py`
- Test: `scripts/e2e/service-s2-08.spec.ts`

**Interfaces:** `RunTrace / FlowVersionDiff / FixedCohortComparison`

**Consumes:** S2-04, S2-05, S2-06, S2-07

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- Undo/Redo·자동 draft 복구·버전 diff·subgraph template·다른 프로젝트의 class/model mapping을 검증한다.
- 동일 frozen cohort의 A/B·선택 노드까지 실행·branch skip 이유를 제공한다. partial run은 최종 OK가 아니며 cache는 정확한 ancestor/input/model/config identity만 재사용한다.
- zoom/pan/fit/minimap/자동정렬/search·패널 resize/collapse와 큰 DAG 탐색을 검증한다. 모델을 여러 클래스와 노드에서 공유해도 node별 score/ROI/rule 의미가 유지된다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s2-08.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s2-08.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S2-09 학습과 작업 센터 공통 화면

**Files:**

- Modify: `src/renderer/components/training/TrainingController.tsx`
- Modify: `src/renderer/components/training/TaskCenter.tsx`
- Modify: `src/renderer/components/training/ProgramWorkbenchControls.tsx`
- Test: `scripts/e2e/service-s2-09.spec.ts`

**Interfaces:** `ModelRecipeEditor / JobProgressView / TaskHandoff`

**Consumes:** S1-04, S1-05, S2-02

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 10개 모델군에서 데이터 준비·실행 위치·예산·진행·취소·재연결·결과 열기를 같은 방식으로 사용한다.
- 수동 ID 대신 호환 부모 모델/평가 결과를 선택한다. 불확실 상태와 실패 원인·서버 연결·예약 자원을 사용자가 확인한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s2-09.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s2-09.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## S2-10 운영자 화면과 개인 설정

**Files:**

- Create: `src/renderer/components/operator/OperatorWorkspace.tsx`
- Modify: `src/renderer/services/projectPreferences.ts`
- Modify: `src/renderer/components/common/OperatorGuidanceBanner.tsx`
- Test: `scripts/e2e/service-s2-10.spec.ts`

**Interfaces:** `OperatorSession(recipe_ref, active_release, input_health) / UserPreferences`

**Consumes:** S1-07, S2-06, S5-01

**Produces:** 이 계약과 versioned 결과/이벤트, 아래 acceptance에 해당하는 실행 근거.

**Behavior assertions:**

- 제품/recipe·검사 시작정지·input health·최근 결과·REVIEW·버전·장애 해결만으로 검사 운영이 가능하다. 권한 없는 편집은 키보드 경로에서도 거절한다.
- 한국어/영어·단축키·도움말·사용자 설정을 일관되게 적용한다. 작업 중 설정 변경과 재시작을 확인한다.

- [ ] acceptance를 실제 production 호출 경로에서 재현하는 failing test를 작성한다. fixture와 expected outcome을 리뷰한다.
- [ ] `npx playwright test scripts/e2e/service-s2-10.spec.ts`로 구현 전 실패 또는 현재 회귀를 확인한다. 이유가 예상한 동작 실패인지 확인한다.
- [ ] 지정 경계에 계약을 구현하고 UI/기존 프로젝트/API의 호환 또는 migration을 연결한다.
- [ ] `npx playwright test scripts/e2e/service-s2-10.spec.ts`와 영향을 받는 기존 focused 검증을 통과시킨다. UI/native/remote/장비 조건은 실제 실행 여부와 receipt를 별도로 남긴다.
- [ ] 독립 reviewer가 behavior·입력경계·누락 legacy feature를 확인한다. 소유 파일만 commit하고 ledger에 source SHA·근거·한계를 기록한다.

## phase 완료 판정

- 해당 task의 계약·영속·재열기·실패·handoff 근거가 존재한다.
- 새로운 interface가 다음 phase 및 legacy ID와 연결되어 있다.
- 테스트 skip/부족 데이터/미지원 target/서명/실장비 미검증은 이유와 owner를 남긴다.
- 조건이 없는 항목의 `not_required`는 사유와 reviewer가 있어야 하며 pending을 임의 완료로 바꾸지 않는다.

## 추가 기능 acceptance 연결

구현 시 spec 확장 계약과 registry의 acceptance를 함께 사용한다. 다른 phase의 owner가 만든 계약은 검토한 뒤 소비한다.

- E01 한 부품의 여러 view를 묶는 입력 계약: owner S5-03; 이 phase 연결 S2-05
- E02 기준 특징에 고정되는 fixture ROI: owner S2-05; 이 phase 연결 S2-05
- E03 실제 치수 교정 artifact와 설정 일치 검사: owner S3-08; 이 phase 연결 S2-05
- E04 검사 규칙 변경의 사유와 before/after 감사기록: owner S5-10; 이 phase 연결 S2-08
- E07 배포 준비 화면의 전체 의존성 점검표: owner S5-05; 이 phase 연결 S2-04
- E08 데이터 입출력·백업도 같은 영속 작업 센터에서 재개: owner S1-02; 이 phase 연결 S2-09
