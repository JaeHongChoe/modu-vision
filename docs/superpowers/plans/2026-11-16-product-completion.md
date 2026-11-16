# 작업 중심 제품 완성도 개선 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 라벨·평가·검사 기능을 작업에 맞게 발견하고 이어 쓰기 쉽게 만든다.
**Architecture:** React presentation을 기존 domain/store/API owners 위에서 정리한다. 저장 ID/값/authority와 실행 handler는 유지하고 native disclosure·목적별 도구·현재 필터를 재사용한다.
**Tech Stack:** 기존 React/TypeScript/Zustand/Lucide, Node24.22.1, Python3.13. 새 제품 의존성 없음.
**Spec:** `docs/superpowers/specs/2026-11-16-product-completion-design.md`

## Global Constraints

- 현재 실행 앱/8443·사용자 자료·계정/TLS·다른 worktree를 변경하지 않는다.
- 기존 save/source/project/labelset/model/readiness/worker 종료/승인 가드를 유지한다.
- 원본 형상·현재 activeTool·선택·실행 상태를 접기/렌더만으로 바꾸지 않는다.
- 간단히/전체 보기, 양 테마, 720px와 sidebar 폭, 키보드 접근성을 유지한다.
- 전문 모델/고정 전처리/도우미 지원 범위를 UI에서 임의 확장하지 않는다.
- 새로운 실제 모델·GPT/외부 이미지 전송·배포·보안 권한 변경은 하지 않는다.
- task 소유 파일을 분리하고 shared index/ref/commit은 root만 변경한다. 다른 작업의 수정은 덮어쓰지 않는다.

## Review Focus

1. 도구를 접었지만 keyboard/existing saved state의 activeTool이 비우선 도구인 경우 현재 도구가 보이고 기존 형상은 유지된다.
2. 원격 취소가 요청됐으나 ACK/worker exit가 미확인인 경우 실행 근거를 접어도 불확실 상태와 필요한 제어가 보인다.
3. 필터/라벨 수정/모델 선택 직후 프로젝트·자료가 바뀌면 기존 controller fences가 오래된 효과를 거절한다.
4. 분류 사진에 클래스는 있고 도형이0개인 경우 미라벨이라고 오해시키지 않는다. 실제 분류 정답 소유자는 바뀌지 않는다.
5. 화면의 미리보기 기준/선택한 흐름과 저장 평가/실행 snapshot을 같은 결과로 표현하지 않는다.

### Task 1: 작업에 맞는 라벨링

**Files:** `components/labeling/LabelingToolbar.tsx`, `LabelingStudio.tsx`, `AnnotationList.tsx`; 필요한 같은 폴더 pure presentation helper와 관련 `.test.cjs`.
**Consumes:** 기존 VisionTask/viewMode/useAnnotationStore/ClassificationClassPicker. **Produces:** 선택 도구를 보존하는 작업별 primary tools와 advanced disclosure, 분류의 선택적 영역 빈 상태.

- [ ] 기존 테스트 setup을 사용해 classification/detection/segmentation/rotated task의 간단히/전체 보기, active 비우선 도구, geometry0 classification 상태의 실패 회귀를 추가한다.
- [ ] Node24에서 예상 행동 실패를 확인하고 RED log를 보존한다.
- [ ] 위 소유 파일에서 순수 표시/그룹화만 구현한다. 기존 handler·save·canvas 수명·단축키를 유지한다.
- [ ] 집중 회귀를 통과시키고 변경·검증·한계를 report에 기록한다.

### Task 2: 결과와 오류 사진 탐색

**Files:** `components/evaluation/EvaluationStudio.tsx`, 필요하면 같은 폴더 `evaluationPresentation.ts`/`EvaluationContextBar.tsx`, 관련 tests. CSS가 필요하면 evaluation 전용 파일.
**Consumes:** 기존 ModelComparisonModel/version records, sampleFilter, saved threshold, guarded evaluation/cancel/open-label handlers. **Produces:** 읽기 쉬운 평가 선택/기술 근거, 실제 필터로 이어지는 결과 요약, simple-view 공간 개선.

- [ ] 한국어 선택 이름/값 ID보존, 결과 필터 클릭, 기술 근거 disclosure와 항상 보이는 원격 취소/오류, binary/전문모델 구분의 실패 회귀를 작성한다.
- [ ] 예상 RED 확인 후 별도 presentation helper 또는 컴포넌트로 표시를 정리한다. 실행/상태 소유권은 원래 controller에 둔다.
- [ ] 단순 뷰에서 고급 지표는 접근 가능하게 접고 사진 목록·비교 공간을 확보한다. 저장 평가와 preview를 명확히 표시한다.
- [ ] 관련 unit/type checks를 통과시키고 report를 작성한다.

### Task 3: 저장 흐름·검사의 읽기 쉬운 맥락

**Files:** `components/flowchart/SavedFlowIdentityCard.tsx`, `components/inference/BatchInspectionPanel.tsx`, 관련 tests; 필요하면 `components/wizard/WizardFooter.tsx`의 표시 부분만.
**Consumes:** 원래 flow identity/snapshot, 현재 선택, 기존 live status/cancel/stop, readiness strings. **Produces:** 흐름 이름·연결 모델 수가 우선인 compact summary와 전체 식별자 근거 disclosure.

- [ ] 현재/과거 실행 identity 구분, ID/hash 보존과 간단히 보기 접힘, 실행/중단/오류 접근성의 실패 회귀를 작성한다.
- [ ] 기존 identity 객체를 변경하지 않고 표시를 구현한다. 실행 시작/선택/이력/readiness handler는 바꾸지 않는다.
- [ ] 집중 회귀를 통과시키고 report를 작성한다.

### Task 4: 통합·화면 검증·현재 문서 정리

**Files:** 관련 browser specs/fixtures, `DESIGN.md`, `UX-CONTRACT.md`, `README.md`, 사용자 안내 및 이번 검증 기록. 변경된 시험·소스의 기존 역사 선언은 원본 이력을 보존한 채 필요한 현재 binding만 갱신한다.

- [ ] 세 task diff를 검토하고 충돌/공통 용어를 정리한다. 실패를 발견하면 실제 RED→GREEN으로 수리한다.
- [ ] Node24 renderer/main 전체, 제품/E2E 타입, build/light-theme, 서비스 계획·행동 근거, premium static audit를 실행한다. 기존 whole-product static debt와 이번 변경을 구분한다.
- [ ] 소유 분리 자료/profile의 실제 UI에서 도구 disclosure/키보드, 결과 필터→라벨 수정, 검사 identity/이력, theme/720px/sidebar, loading/error/stale 동선을 확인한다. 사용자 앱/웹은 재시작하지 않는다.
- [ ] 실제 화면을 캡처·열어 확인하고 before/after를 남긴다. 모델 품질·provider·사용자 수락은 주장하지 않는다.
- [ ] 현재 README의 이미 배포된 OSS/라벨링을 ‘개발 중/미반영’이라고 한 시점 문구를 실제 merge/runtime 증거와 맞춘다. 새 개선은 검증/배포 상태를 별도로 적는다.
- [ ] 새 독립 reviewer에게 spec·전체 diff·검증 기록을 전달하고 중요한 발견을 고친다. root가 명시 파일만 commit/push하고 기존 PR 없이 새 PR을 만든다. CI는 최종 head로만 확인하며 full/fast 재실행·중복 dispatch·취소를 하지 않는다.

## 완료 기준

세 표시 기능의 실제 동작과 관련 회귀가 통과하고, 현재 구현 범위·다음 후속·남은 현장/인증 검증이 구분된 기능 브랜치와 검토 가능한 PR을 제공한다. 기존 실행본 교체나 운영 배포 완료라고 확대하지 않는다.
