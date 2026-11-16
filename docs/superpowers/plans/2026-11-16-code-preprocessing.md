# Programmable image preparation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Preserve other workers' edits.

**Goal:** Editable numeric preprocessing code and embedded-assistant configuration, with an FFT starter and one runtime across model lifecycle paths.

**Architecture:** Add a bounded numeric AST interpreter as a saved recipe operation. Extend the existing editor, scoped assistant action and portable inspector package, preserving canonical guards.

**Tech stack:** Existing React/TypeScript, Python/NumPy/OpenCV; no new runtime dependency.

**Spec:** docs/superpowers/specs/2026-11-16-code-preprocessing-design.md

## Global constraints

No imports/exec/eval/bytecode or arbitrary system access. Same-shape RGB 0..255
with `result` output. Classification/segmentation only. 8192 UTF-8 bytes/32 lines.
No provider calls, original edits, CI duplication or live service replacement.

## Review focus

Unexpected native allocations; wrong FFT spatial axes; stale draft/preview owner;
old checkpoint/export compatibility; examples mistaken for field calibration.

- [x] Backend owner: failing runtime/FFT/binding/export tests, interpreter,
  canonical recipe and export inclusion, then focused verification.
- [x] Editor owner: failing code edit/preview retirement tests, code block,
  reference, FFT starter and novice request flow using shared styles.
- [x] Assistant owner: schema/reference/read tool, mock execution/receipt tests,
  reuse current scoped preview/save action without automatic external calls.
- [x] Root: scoped composer draft handoff, integration E2E, documentation,
  independent review and required local checks.
- [ ] Remote: exact-head fast/full qualification, followed by the separately
  authorized ordered merge and runtime release. Preserve prior CI history.

## Rulings

- Continue the existing isolated feature branch; no extra worktree or service.
- Numeric subset is intentionally explicit; do not label it unrestricted Python.
- Preview verifies execution, not the suitability of noise removal for real data.
