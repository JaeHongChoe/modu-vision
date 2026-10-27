# Assistant Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** GPT가 현재 앱 상태·실제 오류·저장된 실행 결과를 읽고 확인된 근거로 후속 작업을 제안하게 한다.

**Architecture:** 기존 계정 연결과 앱 함수 도구에 안전한 관측 DTO를 연결한다. 현재 스토어와 메모리 요청 이력을 읽고, 작업/작업 원장은 별도 GET-only 경로로 재확인한다. 새 질문에는 최신 요약을 넣고 같은 질문 안에서는 도구로 갱신한다.

**Tech Stack:** TypeScript, Zustand, 기존 REST/Responses 연결, node:test. 새 라이브러리·서비스 없음.

**Spec:** `docs/superpowers/specs/2026-10-27-assistant-diagnostics-design.md`

## Global Constraints

- 기존 12개 실행 행동·승인·중복 실행 방지 유지.
- 공급자 호출은 명시 질문에서만. 폴링·복원·진단은 GPT 호출 0건.
- 도구 JSON 4,000자 미만, 전체 질문 문맥 12,000자 미만.
- 진단 이력 메모리 40건. 요청 본문/헤더/응답 원문/자격증명/파일·이미지 수집 금지.
- 프로젝트·원본·라벨 세트·계정·연결 ABA 변경과 늦은 응답 격리.
- 설치 앱·사용자 자료·기존 서비스·진행 CI 보존. 별도 기능 branch에서 개발.

## Review Focus

- structured HTTP 오류가 `[object Object]`가 되거나 원인을 잃지 않는가.
- GET-only 조회가 restore/resume/reconcile을 호출해 실행을 유발하지 않는가.
- 새 프로젝트나 계정으로 이전 오류/영수증이 섞이지 않는가.
- 긴 24단계 계획·오류가 파싱 불가 JSON이나 현재 오류 생략으로 끝나지 않는가.
- 민감 문자열과 오류 안의 명령문이 정보/실행 권한으로 유출·승격되지 않는가.

## Task 1: 앱 관측과 요청 실패 이력

**Files:** 새 `src/renderer/services/{assistantDiagnostics,assistantDiagnosticsRuntime,apiDiagnostics}.ts`와 인접 테스트. 수정 `api.ts`의 요청 관측 경계만.

**Interfaces:** `diagnosticText(value,limit?) → string|null`, `diagnosticError(cause) → {code:string|null,message:string,status:number|null}`, `appDiagnosticSummary() → Record<string,unknown>`, `readAppDiagnostics(section?) → Promise<Record<string,unknown>>`.

- [x] RED: 현재 화면 밖의 오류, global activeError, structured HTTP/빈 JSON/전송 실패, scope 변경, 민감 정보/길이 검사.
- [x] 기존 스토어의 허용 필드로 summary(2,000자 이하)와 section별 detail(3,600자 이하)을 구성한다. `overview/project/dataset/training/evaluation/flow/inference/compute/requests/jobs`를 지원한다.
- [x] 기존 training workspace tasks의 읽기 경로로만 jobs를 확인한다. 조회 범위·시점과 누락 수를 반환한다.
- [x] offset/next_offset으로 생략된 요청·작업·오류를 읽는다. 긴 항목 때문에 목록이 줄어도 다음 페이지에서 빠진 오류를 건너뛰지 않는 검사로 확인한다.
- [x] `api.ts`에서 실패만 관측한다. 원래 응답·오류 형태·정상 흐름을 보존하고 scope-local 메모리 이력만 기록한다.
- [x] GREEN: 순수 DTO/요청 실제 모듈/스토어 어댑터 검사. 요청 입력·응답의 비밀 값이 이력에 없는지 확인한다.

## Task 2: 원장의 실행 증거와 오류 피드백

**Files:** 새 `assistantTasks/feedback.ts`와 테스트; 수정 `controller.ts`, `runtime.ts`, `useAssistantTaskStore.ts`와 관련 테스트.

**Interfaces:** `controller.summary()`는 정제된 캐시. `controller.observe():Promise<Record<string,unknown>>`는 GET-only 실제 원장 읽기. `assistantTaskTools.cachedStatus()`는 동기 캐시; `status()`는 observe 비동기.

- [x] RED: revision/현재 단계/영수증/원인, GET-only·늦은 조회·조회 실패, 다른 scope 기록, 24단계 길이 검증.
- [x] `summary`에 상태·단계 수·허용한 결과 ID/수치·컨트롤러 오류·freshness를 포함하고 3,400자 이하로 만든다.
- [x] `observe`는 ID/문맥/연결 세대를 고정해 `client.get`만 수행한다. 실패는 `fresh:false`와 확인된 캐시/읽기 오류로 구분한다.
- [x] 기존 어댑터가 이미 읽은 학습·초안·흐름·검사 오류를 정제해 receipt와 메시지에 남긴다. 추가 실행/재시도 없음.
- [x] GREEN: runner/controller/runtime/polling 기존 테스트와 회귀를 통과한다.

## Task 3: GPT 문맥과 진단 도구 연결

**Files:** `assistantRuntime.ts`, `assistantTools.ts`, `assistantSession.ts`, `src/main/assistant/service.ts`와 관련 테스트, `docs/assistant-tasks.md`.

- [x] RED: `read_app_diagnostics`의 실제 비동기 읽기 결과와 현재 질문 문맥의 진단/작업 결과, structured 실패를 가짜 bridge로 검증한다.
- [x] 기존 prefix 고정을 유지하며 질문 시작마다 안전한 현재 진단과 cached task feedback을 넣는다. API 모듈과 task store 간 런타임 import cycle을 만들지 않는다.
- [x] 새 read 도구의 section 인자를 검증한다. 원장 도구는 GET-only observe를 await한다.
- [x] 시스템 지시에 오류/비신뢰 자료·freshness·작업 번호 확인·불명확 결과 재시도 금지·새 계획 확인을 명시한다.
- [x] tool 결과가 한도를 넘으면 유효 JSON envelope로 명시하고 더 작은 조회를 요청한다. JSON 중간 절단 금지.
- [x] GREEN: 가짜 provider가 최신 오류/저장 ID를 받고 후속 답변/새 draft를 제시하되 승인·실행을 하지 않는 왕복 검사.

## Task 4: 통합 검증·독립 리뷰·출판

- [ ] 타입 검사, renderer/main 회귀, 빌드, e2e 타입 및 영향받는 source owner/gate 검증. 새 실패가 있으면 같은 원인만 수리한다.
- [ ] 구현별/전체 branch 독립 리뷰. privacy·scope·read-only·JSON bounds 지적 수리 후 해당 회귀 확인.
- [ ] 검증 기록에 제어된 공급자/합성 상태와 실제 계정·설치 앱 미검증을 구분한다.
- [ ] 단일 기능 브랜치를 commit/push하고 PR20 위 draft PR을 연결한다. 실제 remote SHA/body와 자동 CI 시작을 확인한다. 기존 CI 재실행/취소/새 full 중복 기동 없음.
