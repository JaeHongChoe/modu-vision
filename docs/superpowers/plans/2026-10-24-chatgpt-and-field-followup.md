# Local ChatGPT connection and real-image diagnostic plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. The user already authorized continued implementation; credentials, terms acceptance, paid requests and human quality approval remain direct user actions.

**Goal:** Prepare the app's own protected ChatGPT connection and replace synthetic-only diagnostic evidence with a small reproducible cohort from the user's local images.

**Architecture:** Electron main owns the loopback OAuth listener, protected registrations, rotation and provider transport. Renderer receives public account state only. Existing API-key providers and offline 1–6 steps stay available. Real-image diagnostics run entirely locally against copied, fingerprinted inputs and retained labels.

**Tech stack:** Node/Electron, TypeScript, React/Zustand, node:test; existing Python foundation-labeling engine and cached pinned SAM2/Grounding DINO.

**Spec:** `docs/product-direction-easy-labeling-agent.md` §§4.2–4.7, 13; `docs/qa/first-use-and-field-validation.md`; user follow-up permitting implementation and local/external data use.

## Constraints and decisions

- No other app's tokens/cookies, live account login, implicit terms acceptance, paid calls or external image transmission.
- Separate encrypted registration store; opaque stable host ID; one process owns rotating credentials. No secrets in IPC, renderer, project logs, URLs exposed to UI, or Git.
- Explicit Continue with ChatGPT after terms/privacy acknowledgement; optional integration. Plan scope absent means signed in with inference disabled.
- Discovery/JWKS and API transport use fixed trusted origins, no redirects. Verify ID signature/issuer/audience/expiry/nonce and returning subject before retaining credentials.
- `store:false`, `stream:true`, namespaced tools, stateless history, and terminal `response.completed` are required. First live version is text-only; existing API-key image consent remains.
- Local copied data and retained labels are diagnostic references, not human reconfirmation. Exclude image+inference comparison composites. No training, model downloads or original changes for this diagnostic.
- The already published PR #10 qualification is independent; do not edit its worktree or duplicate its CI. The 15-minute automation stays paused.

## Tasks

1. **Protected account store** — new `src/main/assistant/siwcStore.ts` and behavioral tests: atomic encrypted persistence, public/secret separation, identity/client isolation, exclusive lease, logout retains registration. RED→GREEN focused Node tests.
2. **Connection and transport** — new `siwcConnection.ts` and tests using `siwcProtocol.ts` and the store: actual loopback listener with fake issuer transport, cancellation and stale callback guards, scoped model discovery, persisted refresh rotation, SSE success/failure and local/remote logout distinction. No real registration during development.
3. **App integration** — shared types, service, IPC/preload, setup UI and authority binding. Explicit account/model selection cancels old requests and retires previews. Add failing service/UI state tests before implementation, then renderer/main type checks and existing provider regressions.
4. **Real-image cohort** — private fixed 12-image LabelMe cohort (6 Crack/6 Bow) plus 24-image OK/NG cohort (12 each), source/copy SHA receipts. Evaluate frozen phrase prompts and GT-assisted SAM2 points separately; retain per-image predictions/masks/metrics/failures. No threshold tuning on this cohort.
5. **Final verification and handoff** — whole renderer suite, type/build checks, fresh independent review of auth trust boundaries, isolated real UI mock connection. Publish working code separately after checks; prepare a concrete real-login screen for the user. Report live account, novice timing and operational quality as pending until actually performed.

## Review focus

- Account switching while a callback, refresh, inference or tool preview is pending must not rehydrate or execute under the old authority.
- Restart and a second app process must not race refresh-token rotation or replace a retained registration with an unrelated identity.
- Bad JWKS/discovery, redirect, huge/malformed bodies and truncated/failed SSE fail closed without leaking secrets.
- Model-list visibility is not inference permission; identity-only scope and usage errors have accurate UI actions without automatic paid retry.
- Composite image leakage, duplicate cohort images, GT-assisted prompts and sparse historical labels must remain visible in diagnostic receipts and claims.
