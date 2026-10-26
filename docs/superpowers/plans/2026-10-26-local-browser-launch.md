# Localhost Browser Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Provide an ordinary local browser launcher using the approved HTTPS local-account design.

**Architecture:** A dedicated Python TLS/ASGI entrypoint serves built renderer files outside the unchanged authenticated API app. Persistent web-only paths and a server-instance lock are configured before backend imports; existing cookie/CSRF/project authority and lifespan remain intact.

**Tech Stack:** Existing Python 3.13, FastAPI/Starlette/Uvicorn, cryptography, Node.js 24, Vite, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-26-local-browser-launch-design.md` (user approved).

## Global Constraints

- Default `https://localhost:8443`; bind `127.0.0.1` only; no LAN/public hosting.
- Keep `backend.main.create_app`, existing browser authentication, CSRF, project authority and backend lifespan unchanged.
- Disable forwarded-header trust. Never inject process capability, native bridge or test shim into browser requests.
- Web-only persistent storage before backend imports; preserve all other checkouts and user/QA/guide data.
- No automatic dependency installs, OS trust changes, real external accounts, model downloads or service registration.
- First administrator password is hidden terminal input, never CLI/environment/log output. Browser uses existing HTTPS login.
- Ctrl+C retains existing training detach/recovery semantics; instance lock is not worker ownership.
- Worktree `/Users/kai/.local/share/modu-vision-worktrees/web-roundtrip-20261026`, branch `codex/local-browser-launch-20261026`; baseline `c1bc9954`.
- Python test runtime: `.superpowers/sdd/2026-10-26-web-roundtrip/venv/bin/python`. Preserve that existing environment and all prior QA evidence.

## Review Focus

- A previously created cert/key pair is corrupt, expired, mismatched or insecurely readable: fail without overwriting.
- Proxy/Host/Origin headers attempt to reach another authority: reject or ignore untrusted forwarding without changing API identity.
- Static path variants and links target local private files: never return data, keys, source maps or source files.
- Launch is interrupted or a second process uses the same data/port: release only owned server resources and never terminate another process.
- Reload/restart loses tab CSRF state or telemetry: reauthenticate and reopen the same saved records; verify WSS directly.

### Task 1: Persistent local web state, TLS and instance ownership

**Files:** Create `backend/local_web_state.py`; test `backend/tests/test_local_web_state.py`.

**Interfaces:**
- Produces `WebPaths` (frozen dataclass): `root`, `projects`, `auth`, `tls`, `splits`, `thumbnails`, `flow_templates`, each a `Path`.
- `web_paths(data_dir: str | Path | None = None) -> WebPaths` chooses platform user-data root, defaults to `Modu Vision Web` on macOS/Windows and `modu-vision-web` under Linux user data.
- `configure_environment(paths: WebPaths) -> None` pins all discovered mutable global paths before engine imports; no HOME override.
- `instance_lock(paths: WebPaths)` context manager: exclusive OS-backed same-root server lock, released on normal/error exit without deleting user data.
- `ensure_tls(paths: WebPaths, cert_file: Path | None = None, key_file: Path | None = None) -> tuple[Path, Path]`: generate own localhost certificate/key only when both absent; validate SAN, dates, key match/private permissions; existing invalid material fails intact.

- [ ] Write test-first cases for platform path choice, env isolation, competing process lock/reacquire, TLS generation/reuse, partial pair/mismatch/expiry/SAN/key permissions and link refusal.
- [ ] Run the focused tests and record expected missing-feature RED evidence.
- [ ] Implement the support module without importing backend.main, changing shared environments or global trust stores. Discover any additional mutable environment defaults with a bounded source read and pin those to the web root.
- [ ] Run focused tests plus relevant existing account/auth tests; record named failures and distinguish environment problems. Run `git diff --check`.
- [ ] Self-review, commit only this task's files, and write full RED/GREEN/changes/concerns report to the supplied report path.

### Task 2: Product HTTPS server, first account, launch command and guide

**Files:** Create `backend/local_web_app.py`, `backend/web.py`, `scripts/local-web.cjs`, `backend/tests/test_local_web_app.py`, `backend/tests/test_local_web_cli.py`, `scripts/tests/local-web.test.cjs`, `docs/local-browser.md`; modify `package.json`, `README.md`, `docs/extension-contracts.md` only where needed.

**Interfaces:**
- Consumes all Task 1 interfaces verbatim.
- Produces `LocalWebApp(api_app, renderer_dir: Path, origin: str)` ASGI dispatcher: lifespan/full API/health/WS paths preserved; static GET/HEAD only and contained public build files; exact Host/scheme/Origin guards.
- `python -m backend.web [--data-dir PATH] [--port PORT] [--cert-file PATH --key-file PATH] [--no-open]` runs direct Uvicorn TLS with forwarded headers disabled. Default port 8443; port 0 is allowed for an OS-allocated local port, and actual port defines origin.
- Node wrapper resolves `VISION_AI_PYTHON` or existing repo virtualenv/Python, builds current renderer, forwards CLI args with no shell interpolation, and returns child status/signals. `--help` must not start a build or server. `npm run web` calls this wrapper.
- Initial account uses hidden terminal password/confirmation plus existing AccountStore bootstrap before serving; existing accounts are preserved. Noninteractive empty first-run refuses with a clear setup command. Do not expose bootstrap via browser.
- After real owned HTTPS `/health` readiness, output `MODU_WEB_READY=<JSON>` with public `url`, `pid`, `data_dir`, `certificate_file`, `certificate_sha256` only, then optionally open default browser. Never log password, cookie, CSRF or API capability.

- [ ] Write failing tests for unauthenticated static vs guarded API, exact Host/Origin, spoofed forwarded headers, API/WS/lifespan routing, malformed/encoded traversal and linked files, MIME/method refusal, secret/map non-serving.
- [ ] Write failing CLI/wrapper tests for default/invalid options, terminal bootstrap privacy/refusal/reuse, lock and port conflicts, readiness and owned cleanup, argv/no-shell behavior and help-without-build.
- [ ] Record RED then implement separate modules and launch command. Pin storage before imports, bind socket before creating origin, preserve backend lifespan and graceful detach. Keep product code independent of e2e fixtures.
- [ ] Write source-environment setup/run/restart/login/certificate fingerprint/error instructions; state browser native capability limits and physical GPU sharing. Include macOS, Linux and Windows command syntax without claiming all OS tested.
- [ ] Run task tests, relevant original authentication/host tests, e2e types, application typecheck, service/action evidence gates and diff check once. Record exact outputs/named failures; do not weaken assertions or source-pinned history.
- [ ] Self-review and commit; report exact test evidence and any concerns.

### Task 3: Actual launcher browser round trip and closure evidence

**Files:** Create focused integration test/runner under `scripts/qa/local-browser-acceptance.cjs` with `scripts/tests/local-browser-acceptance.test.cjs` as needed, and public `docs/verification/2026-10-26-local-browser.md` / `.json`; keep run artifacts in this plan's private workspace.

**Interfaces:**
- Consumes product CLI and `MODU_WEB_READY` JSON from Task 2.
- Uses actual unshimmed browser and product TLS ASGI server, an isolated synthetic local account/data root and existing cached official `resnet18-f37072fd.pth` (validate SHA, no downloads).
- Production launcher receives no browser certificate bypass flags. Only the isolated QA browser may trust its generated test certificate.

- [ ] First write a failing integration assertion against the real launcher or its acceptance runner for a saved-record/restart behavior not yet covered; record RED before any acceptance helper implementation.
- [ ] Add bounded owned-process test setup/teardown and redacted receipts; never read actual user accounts or credentials. Do not use the old browser_team_server proxy or start repeated model runs.
- [ ] Perform actual browser login and UI project creation, synthetic ZIP upload/adoption/source selection, label save, split, one small real ResNet18 training and held-out evaluation. Confirm saved job/evaluation and hashes after product server restart and a new browser login.
- [ ] Verify actual same-origin WSS telemetry open/message and reconnection, plus default unauthenticated refusal/Host/Origin behavior against product entrypoint. Close only owned QA processes and confirm ports close.
- [ ] Run meaningful related regression checks after any new fixes, record saved artifacts vs browser/engine/model/OS boundaries, and publish an accurate compact evidence summary. Preserve prior failure history.
- [ ] Self-review and commit all task evidence/code, leaving private synthetic data and secrets untracked/ignored. Final independent branch review follows.

## Execution choice and self-review

The user's “설계대로 진행” authorizes implementation, so no repeat general approval is needed. Controller selects subagent-driven execution with per-task reviews under the active collaboration instruction. Tasks share only the interfaces above and execute sequentially; independent read-only audits may run concurrently. Default Python/Node dependencies already exist and must not be reinstalled blindly.

Coverage check: launch/auth/static/TLS/storage/lock in Tasks 1–2; actual persistence/model/WSS in Task 3; review-focus failure cases assigned to their owning tasks. No new main merge, production deployment, background schedule or forced CI action is part of this plan.
