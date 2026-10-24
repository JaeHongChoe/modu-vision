# Primary panel errors implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Complete the existing design's shared explanation of errors already visible in the first-project workflow.

**Architecture:** Reuse `registerPanelError(source, text)` and owned cleanup. Current panel errors belong to the selected project, source, task and label set; scope changes and hidden/unmounted panels retire registrations. Existing `useScopedError` rejects old callbacks where needed. No DOM scraping or new provider/network behavior.

**Tech Stack:** React, TypeScript, Node component contract tests.

**Spec:** `docs/product-direction-easy-labeling-agent.md` sections 2.3, 4.1 and 5.4; existing user authorization to finish the original design.

## Global constraints

- Preserve original images, confirmed labels, fingerprints, review and approval guards.
- No real credentials, paid calls, image external transfer, model or training reruns for this change.
- Keep the 15-minute monitoring automation paused.
- Do not use the old head's CI as evidence for changed code.

## Review focus

- Different project/source/task/label set must not inherit previous errors.
- Late failed callbacks after navigation must stay retired, including A to B to A.
- Hidden or unmounted panels must remove only their own registration.
- Actual displayed errors must reach the footer and assistant's existing read-error tool.
- Resolving an error must clear shared guidance without rerunning the operation.

## Tasks

- [x] DatasetStudio and TrainingController: failing component behavior tests; connect current visible errors; verify cleanup and transition behavior.
- [x] PhraseDraftCreate and ModelAssistPanel: failing component behavior tests; connect errors only while their panel is visible and current; verify hide/transition/late completion.
- [x] BatchInspectionPanel: failing component tests for count, saved flow and history errors; scope the existing error state; register visible errors with owned cleanup.
- [ ] Run affected tests, renderer suite, types and evidence checks; independently review the diff; publish one meaningful follow-up commit and qualify the new head before merge.
