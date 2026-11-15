# Studio CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax for tracking.

**Goal:** Add a complete authenticated HTTP CLI surface and connect its catalog to GPT.
**Architecture:** Live OpenAPI-derived catalog, standard-library CLI, bounded assistant discovery/observation using the existing execution system.
**Tech Stack:** Python3.13/FastAPI, TypeScript/Node24, existing test runners; no new dependency.
**Spec:** docs/superpowers/specs/2026-11-14-studio-cli-design.md

## Global Constraints
- Source branch from main08066611; keep PR26 independent and user app/web/data unchanged.
- Exact metadata GET alone may skip project selection; authentication is unchanged.
- No GET-implies-safe rule, retries, shell execution, authority widening, model/provider calls or deployment.
- JSON UTF8, explicit scope, standard TLS validation and no secret output.

## Review Focus
- GETs which compute, multi-header credentials, endpoint/cross-origin redirects.
- Mutations accepted before disconnect and ambiguous outcomes.
- Traversal/encoded separators and multipart header injection.
- Binary checksum failures, existing output preservation and large replies.
- Catalog lists vs actual installed routes, stale assistant scope and provider redaction.

### Task1: Catalog (backend agent)
Files: backend/automation/catalog.py, backend/api/routes_automation.py, backend/main.py, backend/api/shared_authorization.py; backend/tests/test_automation_catalog.py.
- [x] Write and observe failing completeness/effect/auth/no-project/lookalike tests.
- [x] Implement catalog contract, safe observation allowlist and existing action mappings.
- [x] Run focused tests; report exact counts and contract.

### Task2: CLI (root)
Files: backend/studio_cli.py, backend/automation/client.py, backend/tests/test_studio_cli.py.
- [x] Write failing subprocess tests using an owned temporary HTTP fixture.
- [x] Implement CLI parsing, catalog retrieval, validated calls, JSON errors, multipart/raw upload and verified binary output.
- [x] Verify no retry, redirects refused, credentials redacted and job acceptance not completion.

### Task3: GPT integration (assistant agent)
Files: src/renderer/services/assistantCommandCatalog.ts and tests, assistantTools.ts, assistantRuntime.ts, src/main/assistant/service.ts.
- [x] Write failing lazy-discovery/safe observation/stale scope/schema-budget tests.
- [x] Add optional dependency-injected discovery and observation registry; wire existing request transport.
- [x] Describe exact existing task-action mapping; prohibit arbitrary API write and unsafe GET.
- [x] Run focused tests, typecheck and related task regressions.

### Task4: Integration/documentation/review
Files: docs/studio-cli.md, README.md, docs/verification/2026-11-14-studio-cli.md, generated types if needed.
- [x] Verify real isolated backend catalog and CLI project/label/job reads without models.
- [x] Document six-stage examples, auth, error/async contracts and native/headless boundaries.
- [x] Run renderer/type/build, focused backend, generated checks.
- [x] Independent review and fix real issues with regression tests.
- [ ] Commit/push separate feature branch and draft PR; report local vs CI vs unverified/installation state accurately.
