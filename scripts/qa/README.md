# Real OS assistant key storage QA

`assistant-safe-storage.cjs` runs the production `AssistantKeyStore` with Electron's actual `safeStorage`. It saves one hardcoded, non-secret dummy, exits, reads/decrypts it in a new Electron process, removes it, and confirms removal in another new process. It never starts the application renderer or backend or calls an assistant provider.

```sh
node --test scripts/tests/assistant-safe-storage-qa.test.cjs src/main/assistant/keyStore.test.cjs
node scripts/qa/assistant-safe-storage.cjs
```

The runner prints its new artifact directory. To select a destination, add `--out-dir /absolute/path/to/a/new-directory`; any existing path is refused. `--prepare` only creates the isolated app/profile and records `prepared`. Preparation and the Node tests do not count as real OS storage verification.

Each run gets a UUID app name, separate `userData`, session data, logs and crash dumps. The child environment excludes API credentials, application paths and Node/Electron startup flags. There is no mock-keychain or plaintext fallback. The native home/session remains available for real OS storage. On macOS, [Electron 44.5.1 sets its Keychain service and account from the app name](https://github.com/electron/electron/blob/v44.5.1/shell/browser/electron_browser_main_parts.cc#L636-L639), so this QA uses its own service rather than the application's or another app's entry.

The runner does not approve OS permission prompts. If a call blocks, its 20-second bound ends the owned process and leaves an incomplete summary plus the last phase's `*-progress.json`. Record the exact prompt without approving it; a timeout is not a pass. [Electron documents that synchronous Keychain calls can block for user input](https://www.electronjs.org/docs/latest/api/safe-storage#synchronous-api).

`summary.json` must report four passed phases with separate PIDs. The per-phase receipts record Electron/platform versions, OS protection availability, file hashes, public `hasKey`, removal, file/folder modes and attempted network requests. They contain no plaintext or ciphertext. The read receipt must match the save file hash, and the two removal receipts must report `hasKey: false`. `run.json` pins the production key-store/provider source hashes.

The dummy provider key is removed from this run's `providers.json`. On macOS, the UUID service's OS encryption master may remain in Keychain; the harness deliberately does not enumerate, read via Keychain tools, or delete Keychain entries. Its exact QA service/account are in `run.json`.

This verifies the source key-store integration in the installed Electron runtime on the current OS. It does not verify paid API access, image transmission consent, signed packaged application identity, other operating systems or human acceptance.

# Native ChatGPT agent composition QA

```sh
node scripts/qa/chatgpt-native.cjs /absolute/path/to/a/new-directory
```

This separately builds and launches the production main/index, IPC, sandboxed preload, renderer and owned backend with a UUID app/profile. It uses generated RSA/JWKS, generated tokens, fake OpenAI HTTPS responses, captured browser opening and `--use-mock-keychain`. The production localhost OAuth callback remains real. It never opens actual login, reads another app's credentials, uses a subscription, trains a model or transfers images.

The case verifies identity-only refusal, explicit plan reconsent/PKCE, the first-use dialog, model selection, namespace status/read/preparation tools, declined and confirmed real split writes, persisted welcome/limit state after restart, user-controlled resumption with zero automatic sends, and explicit encrypted stale-store recovery. Every phase owns a real backend and checks its PID exits and its port closes. Source hashes, process receipts and screenshots stay in the new folder. Generated credential logging or non-loopback provider network attempts fail the run.

The stale lease uses a genuinely exited fixture process PID after normal app shutdown; it tests crash-state recovery without killing a live app/backend. The encrypted original is archived before all saved ChatGPT credentials are cleared, while registration and selected model remain. Unknown/legacy/live-owner refusal is also covered by `siwcStore.test.cjs`.

This is native software composition with controlled provider output. Actual account eligibility, subscription inference/limits/refresh/revocation, terms acceptance, real OS/package identity, human timing and field-model quality remain separate verification.

# Prepare real-account acceptance

```sh
node scripts/qa/chatgpt-account-acceptance.cjs --out-dir /absolute/path/to/a/new-directory
```

The default only prepares and builds an isolated production main/preload/renderer app. It does not launch Electron, access Keychain, open login, consent, load remote models, send a question or revoke a connection. Existing directories, files and symlinks are refused. Each run has a UUID package name and separate user/session/log/crash directories. Build and launch children inherit only native OS session variables; API credentials, Node hooks, app paths and mock-keychain switches are excluded. An explicit `--python /absolute/path/to/python` selects the backend interpreter; the default is `python3` (`python` on Windows). No dependencies or models are downloaded.

After reviewing the prepared files, open that same app from an interactive terminal:

```sh
node scripts/qa/chatgpt-account-acceptance.cjs --run-dir /absolute/path/to/the/prepared-directory --open
```

`--out-dir NEW --open` also prepares/builds and then opens. Electron is spawned directly, without Playwright's Electron loader or any debugger/inspector. A bootstrap sets isolated native directories before loading production main, and carries only fixed acceptance commands over owned stdin/stdout. It evaluates the projected probe in the normal production window through `webContents.executeJavaScript`; the production preload and IPC remain in use. Runtime `app.commandLine` checks refuse mock-keychain, password-store and debugging switches before main loads and before accepting commands. There is no provider fixture, browser replacement or generated credential. The user handles any OS prompt, reads the account terms and transmission notice, logs in using the app's system-browser button, enables plan usage if needed, acknowledges the notice, explicitly loads models and chooses one. The runner does not click those controls. This source build does not establish signed-package identity or cross-platform acceptance.

Before every open/reopen, a sanitized subprocess checks the exact imports required by the production supervisor and resolves that Python's absolute executable path. Failure or timeout refuses the Electron launch, without another interpreter or an installer fallback. Choose a locally complete interpreter explicitly if needed; raw import diagnostics are not included in the receipt. Malformed receipt files refuse before launch, and persistence failures after launch still close the owned app.

The build helper's exact `app/scripts` symlink is moved into this run's `build-support/scripts-source-link`, preserving it and the repository target. The QA runtime therefore has no `scripts/bootstrap_env.py`. Build, open, reopen and native startup all verify that omission, so even a later supervisor dependency-check failure cannot start its package-install fallback. The production supervisor and repository scripts are unchanged.

The terminal accepts `status` for local projected readiness metadata (including only the backend health boolean from its existing bridge) and `quit` for normal app/backend shutdown. Backend PID, port and Python paths are not exported. It saves no raw state, authentication URLs, account labels, identities, tokens, prompt/answer bodies or screenshots. `run.json` contains operational paths and source hashes; `receipt.json` contains only projected observations. Prepared runs refuse source changes before reopening. All artifacts, including the isolated profile, remain in place; the runner does not sign out, delete credentials or remove OS storage entries automatically.

An ordinary UI question advertises the app tools and may make up to six provider calls. To verify exactly one inference request, obtain explicit permission for that consumption, then type this exact terminal command:

```text
I CONSENT TO ONE CHATGPT TEXT REQUEST
```

The check calls the existing production preload/main bridge once with a fixed benign connection question, `context: {}`, `tools: []` and no image. It does not use the renderer conversation/tool loop or write a project execution log. A durable attempt marker is saved before the call; failed or uncertain attempts are never retried, including after reopening. Check `status` first: even a refused/not-ready attempt consumes this run's single check. This does not verify refresh expiry, usage-limit recovery, revocation or model quality, and it must never be run merely to provoke those states.

Completion means the production SIWC parser accepted `response.completed`, with a nonempty final text and no tool call. The receipt records text length and a completion boolean rather than the answer. The current production connection does not forward successful provider request IDs; the helper does not infer one. Before opening, `actualAccountOrPlanInference` is false; after human controls become available it is unknown (`null`) until this check proves completion. Status alone is never account-inference acceptance.

No-network boundary tests:

```sh
node --test scripts/tests/chatgpt-account-acceptance.test.cjs
```

These use controlled bridge/build/launch boundaries and do not launch Electron or call a real provider. Test scratch directories are preserved and printed for inspection.
