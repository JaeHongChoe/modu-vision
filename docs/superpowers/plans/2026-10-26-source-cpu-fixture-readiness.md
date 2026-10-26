# SOURCE CPU fixture readiness implementation plan

> Use superpowers:subagent-driven-development task-by-task.

**Goal:** Make the drain-purpose SOURCE CPU fixture enter only after its genuine completion post-send validation is settled, while retaining the real busy refusal.

**Architecture:** Original fixture profile hooks plus a small test-only completion observer/wait helper; no production policy changes.

**Spec:** `docs/superpowers/specs/2026-10-26-source-cpu-fixture-readiness-design.md`

## Global Constraints

- Preserve generic nonblocking fail-closed admission, frozen-only eligibility, real request/proof/receipt/settlement and all existing assertions.
- No retry/skip/timeout increase or renewed deadline, CI mutation or other branch edits. Root owns publication/qualification and integration.
- Do not copy full diagnostic harness into product tests or emit private frames/locals/credentials.

## Review Focus

Exact original identities and post-send phase, true normal return, no ready marker on error/expiry/foreign context, same original deadline, real lock/refusal preserved, finally cleanup/primary traceback and existing full test selection.

### Task 1: Add genuine bounded completion readiness to the existing fixture

**Files:** backend/tests/test_application_launch_quiescence_bridge.py, new backend/tests/_source_cpu_completion_boundary.py, docs/qa/2026-10-26-source-cpu-fixture-readiness.md, root-created plan/spec.

- [x] Read retained diagnosis/probe and verify relevant CPU source bytes match3944fa54. Record base and before hashes. Local accepted mechanism does not identify hosted exact ordering.
- [x] Add meaningful RED for missing scheduling readiness with actual pinned-lock verify and publication contention, plus separate real original busy/recovery/no-drain negative controls. Keep their distinction explicit.
- [x] Compose existing original service-thread profile to publish readiness only on genuine completion post-send validator normal return, checking original code/thread/cache/queue/request/root/nonce/fixture identity and actual deadline. Controller prefix waits before genuine verification under parent-captured original50s budget and genuine action deadline; then removes only its observer and runs real verification/publication unchanged.
- [x] Factor minimal helper; original exception unwind cannot mark success. Add focused selector/normal-return/deadline negatives, expired/already-present foreign marker refusal, and owned-gate cleanup that preserves primary errors. Test them in existing selected test module.
- [x] Preserve existing changed_nonce/identical replay and all ordinary-stop/registry/source/lease assertions, original3s/4s shutdown deadlines, returned SOURCE settlement proof and existing profile behavior. Add bounded safe failure diagnostics without collecting private state.
- [x] Run new helper controls, real held-lock/refusal and released readiness cases, and existing affected quiescence/CPU execution controls once. No broad full backend or high-count loop. Save exact RED/GREEN commands and failed authoring attempts, environment and source hashes.
- [x] QA distinguishes hosted failure, sufficient local interleaving and test-only repair; no production fix/actual hosted root-cause claim. Diff-check and self-review complete; scoped commit authorized.
- [ ] Independent review and final PR12 delta review before root publishes a new exact head/full once.
