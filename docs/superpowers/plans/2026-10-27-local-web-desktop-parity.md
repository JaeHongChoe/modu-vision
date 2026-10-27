# Local Web Desktop Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Implement the approved localhost browser/native-helper design and verify every desktop feature or its identical prerequisite refusal without desktop shims.

**Architecture:** Python's existing localhost launcher owns a windowless Electron helper through private framed stdio. Same-origin authenticated HTTP endpoints authorize actor/session/tab/project and dispatch a fixed native operation allowlist. Browser transport remains browser transport; local and remote authority and project registration identity remain separate.

**Tech Stack:** Existing Electron 44.5.1/TypeScript, React/Zustand, Python 3.13/FastAPI, SQLite, pytest/node:test/Playwright. No new package installation in user environments.

**Spec:** `docs/superpowers/specs/2026-10-27-local-web-desktop-parity-design.md` (user approved implementation 2026-10-27).

## Global Constraints

- Preserve `https://localhost:8443`, local account authentication, exact Origin/Host, CSRF, existing shared/LAN restrictions and current desktop/web data.
- No `window.api` injection or desktop capability token proxy, generic shell/RPC endpoint, plaintext credential fallback, real login/model/paid calls or service registration during automated QA.
- Selected paths require operation-bound server grants and reauthorization; restore preserves manifest IDs while adding registration/workspace identity.
- Existing OS/signature/role/model prerequisites remain refusals; unverified platforms and user-assisted account/hardware acceptance are reported separately.
- Root owns Git index/commits and launcher integration. Independent implementers edit only assigned files and never stage/commit other work.

## Review Focus

1. Account expiry/disable during chooser, import, OAuth or streaming: consume nothing and never deliver a late result to another authority (Tasks 1–5).
2. Two tabs with the same cookie but different project/model/remote connection: selections, cancellations and writes remain isolated (Tasks 1–5).
3. Same project ID at restored and original locations: refs/cache/jobs and membership retain exact workspace/location (Task 3).
4. Helper crash or uncertain apply response: reconnect reads receipts and never repeats a write/login/update (Tasks 1,2,5).
5. Path/link/target replacement between preview and publish: refuse safely and retain original/staging evidence (Tasks 1,3,4).

## Interface contract

- Helper input is one JSON frame per line, maximum 8 MiB: `{id,actor_id,session_id,tab_id,method,args}`. `id` is a request correlation ID; identities are server supplied. Output is `{id,ok,result}` or `{id,ok:false,error:{code,message}}`; native secrets never appear. `ready` frame is `{event:'ready',protocol:1,platform}`. Logs use stderr. Unknown method/oversized input fails without executing.
- Fixed helper methods: `host.approve`, `dialog.folder`, `dialog.file`, `shell.open`, `assistant.*` corresponding to the existing AssistantBridge, and `updates.*` corresponding to existing host.updates. Session dispose revokes pending calls. Production entrypoint is `src/main/localHost/entry.ts`; it opens no BrowserWindow.
- HTTP controller prefix `/api/local-host`: `GET /capabilities`, `POST /session` creates server tab ID, `POST /approve`, `POST /dialog/folder`, `POST /dialog/file`, `POST /open`, `POST /assistant/{action}`, `POST /updates/{action}`, plus operation-specific project and remote endpoints. These are explicit route/argument allowlists, not arbitrary method names supplied to IPC.
- Every host mutation sends `X-Vision-Local-Tab`; normal API calls may send `X-Vision-Path-Grants` JSON IDs. Controller derives session identity from authenticated server state, not browser identity fields. `app.state.local_host.check_path(scope,key,value,project)` validates a pending grant; `commit_grants(scope)` consumes after full authorization and before downstream action. Missing controller preserves existing denial.
- Renderer `browserHost` exports `available()`, `initialize()`, `reset()`, `can(capability)`, `selectFolder(options)`, `selectFile(options)`, `openPath(path)`, `assistant()`, `updates`, and `requestHeaders(path,method,body?)`. Selection options add `purpose` without changing desktop signatures; grant IDs stay in tab memory.
- Server local-project component exports `LocalProjectRegistry(root)` with authenticated registration/selection, `install_local_projects(app, registry)` and router factory consuming controller native grants. It supplies an explicit local-only resolver to existing authorization and never rewrites manifest IDs.

### Task 1: Private helper client, HTTP controller and path grants (root)

**Files:** Create `backend/local_host/{__init__,client,controller,grants,routes}.py`, tests `backend/tests/test_local_host_{client,grants,routes}.py`; modify `backend/web.py`, `backend/local_web_runtime.py`.

**Interfaces:** Consume helper protocol and local-project resolver; produce controller/hooks/explicit HTTP contract above.

- [x] Write failing tests for framing/correlation/EOF/timeouts, unauthenticated/CSRF/foreign-origin access, wrong actor/tab/project, single-use selected path and replaced link/secret path.
- [x] Run `python -m pytest backend/tests/test_local_host_client.py backend/tests/test_local_host_grants.py backend/tests/test_local_host_routes.py -q`, observe meaningful missing-feature RED.
- [x] Implement owned helper build/spawn/close, bounded line protocol, route authority and path grant store. Only `backend.web` installs capabilities; generic shared app remains unchanged.
- [x] Run new tests plus `test_local_web_app.py`, `test_local_web_cli.py`, `test_local_web_state.py`; expect no failures. Record raw logs.
- [x] Commit this task's owned files only after integration checks.

### Task 2: Native helper, actor-owned assistant and deployment operations

**Files:** Create `src/main/localHost/{protocol,service,entry}.ts` and matching `.test.cjs`; extract reusable native open classification if needed; modify assistant structural interfaces only when necessary, preserving desktop behavior. Do not edit renderer/Python files.

**Interfaces:** Consume/produce framed helper protocol from contract. Export `LocalHostService` with injected native adapters for real-code unit tests; entry injects Electron dialog/shell/safeStorage and existing services.

- [x] Write and run failing tests for unknown methods/invalid identities, per-session/tab isolation, actual native adapter cancellation, no plaintext storage, stale/disposed responses and bounded input.
- [x] Implement native owner approval and folder/file/open operations, actor credential ownership with tab selection/cancel separation, all existing AssistantBridge actions and updates allowlist. Reuse existing SIWC/keyStore/service/distribution/portable checks rather than bypassing them.
- [x] Run `node --test src/main/localHost/*.test.cjs` and existing assistant/native-open/distribution/portable tests appropriate to touched code; expect PASS. Run main typecheck.
- [x] Root reviews diff, then commits only these files.

### Task 3: Account-scoped registration identity, project open/backup/restore

**Files:** Create `backend/local_host/projects.py`, tests `backend/tests/test_local_web_projects.py`; modify `backend/api/shared_authorization.py`, account/project context and archive modules only as required for explicit local resolver. Do not edit root controller/client/launcher or renderer files.

**Interfaces:** Produce `LocalProjectRegistry(root)`, `install_local_projects(app,registry)` and router factory; use controller `check_path`/`commit_grants` hooks. Declare exact integration signatures in report before root wiring.

- [x] Reproduce current account project/open refusal and backup destination contradiction; add RED tests for permitted local operation and unchanged ordinary shared denial.
- [x] Implement additive registration/location IDs with original default selection compatibility, namespace-aware authorization/ref/job lookups, project create/open/import and durable backup/export/restore operations.
- [x] Pin original/restored same-ID locations to distinct canonical workspace contexts; test two tabs, owner/membership revocation, stale expected source, journalled publish/register failure and restart recovery. No identity rewrite, overwrite or permanent deletion.
- [x] Test APIs through real FastAPI/AccountStore/temp files; run relevant shared-account/context/archive/restore suites. Expect PASS; report pre-existing failures separately.
- [x] Root reviews and commits only this stream.

### Task 4: Browser capability adapter and all native-feature consumers

**Files:** Create `src/renderer/services/browserHost.ts` + tests; modify `hostAdapter.ts`, `api.ts`, browser-session integration and relevant project/dataset/mask/label/model/report/runtime/update UI; tests beside changed components. Do not edit main/Python files.

**Interfaces:** Consume explicit HTTP contract; export browserHost/requestHeaders contract above. Preserve `window.api` absence and browser origin routing.

- [x] Add RED tests showing current browser refusal, real request payloads/CSRF/tab/grant scoping, stale logout/project response cancellation and browser report bytes/download.
- [x] Implement capability handshake and host adapter; add picker purpose at each consumer. Project operations use native local endpoints instead of shared-denied generic routes; labels/masks/model imports and report downloads/view use supported feature endpoints. Preserve typed/upload fallbacks.
- [x] Wire assistant/update bridges without constructing fake Electron API. Use server-provided capability/refusal reasons. Add one-shot run attribution header to the actual confirmed write only.
- [x] Run focused renderer tests, complete renderer suite and renderer/main/e2e typecheck after integration. Expect PASS; no source-text mirror tests.
- [x] Root reviews and commits only these files.

### Task 5: Remote authority proxy, lifecycle and deployment integration (root)

**Files:** Create `backend/local_host/remote.py` + tests and renderer remote transport adapter/tests; modify controller/launcher integration and capability coverage.

**Interfaces:** Bound connection key = local session/tab + remote origin/account/epoch; remote context is separate payload/header construction and never local host authority.

- [x] Add RED tests for remote login token leakage, local cookie/upstream Set-Cookie forwarding, arbitrary URL/redirect, local expiry, two tabs, WSS switch and stale response.
- [x] Implement dedicated remote login/logout and allowlisted bounded HTTP/WSS relays; keep credentials server-side and remote authorization on remote server. Invalidate local session connections without cancelling other tabs.
- [x] Integrate native helper startup readiness, owned shutdown/crash reports, operation receipt recovery, distribution/portable/OS-service host approval conditions.
- [x] Run remote/host/shared regression tests and actual owned helper lifecycle smoke; preserve active user services.
- [x] Commit integrated root changes after all stream tests pass.

### Task 6: Complete parity matrix, actual localhost QA, docs and independent review

**Files:** Create `docs/verification/2026-10-27-local-web-desktop-parity.md`, browser E2E/native QA harness evidence under this plan's ignored workspace; update `docs/local-browser.md`, extension contracts and startup/help text as behavior changes.

**Interfaces:** Consume all implemented features. No fake preload/desktop shim counts as product browser evidence.

- [x] Turn all 26 audit rows and design feature rows into checked cases with actual result/artifact or same prerequisite refusal, and explicitly pending user-assisted evidence.
- [ ] Run actual isolated HTTPS launcher/account/project and native chooser flow, uploads/folder and labels/masks, backup→same-ID restore→separate reopening, HTML/JSON download/view and reconnect.
- [x] Run one small synthetic CPU training/evaluation with cached approved weights, saved flow/inspection and reload/restart readback. Do not start duplicate active jobs or send actual assistant/provider requests.
- [ ] Use fake provider at network dependency boundary for actual browser assistant setup/models/read/write-preview/cancel/confirm/log; test account/tab/remote isolation and secrets nonexposure.
- [ ] Run full relevant renderer/main/backend/browser regression, source pins and independent task/whole-branch review. Fix material findings with RED→GREEN, retain failures and missing real-account/device/public-release acceptance honestly.
- [ ] Update actual documentation and completion ledger. Create a reviewable feature PR when code is validated, attach it, and keep exact-head CI separate from local pass. No automatic main merge/deploy from this design approval.
