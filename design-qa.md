# Selected novice workspace: design QA

final result: passed

Scope: source UI implementation on `refactor/novice-workspace-20261114`, verified on 2026-11-15 KST. This is not installed-package acceptance or field model-quality acceptance.

## Comparison and evidence

The user selected **round 2, option 3**. The selected reference and final browser capture were opened together in [comparison.png](docs/verification/assets/novice-workspace/comparison.png), then the six stages, dark/assistant, narrow and native captures were inspected separately.

- Reference: [selected-design.png](docs/verification/assets/novice-workspace/selected-design.png), 1487 × 1058.
- Renderer: [implemented-data.png](docs/verification/assets/novice-workspace/implemented-data.png), 1440 × 1024, light theme, classification example with 64 synthetic photos, data step.
- The comparison scales each capture to the same width. Their aspect ratios are close; no pixel-exact match is claimed. The reference is a generated direction with curated photo order; the implementation uses the real dataset ordering and saved `NG` / `OK` class names.
- The implementation is React UI and real dataset thumbnails. The reference raster is documentation only and is not displayed as an application screen.

## Findings and iterations

No unresolved P0–P2 finding remains in the verified scope.

1. **Resolved P2: repeated introductions and excessive controls before the photos.** The first capture retained a large checklist and duplicated summaries. Model/data management moved into named disclosures, while a compact warning/count row remains visible. The first final-position assertion still failed at 466 px; spacing was corrected rather than weakening the 450 px bound. The final first image starts at approximately 448 px and the test waits for six decoded thumbnails before capture.
2. **Resolved P2: uneven title styling.** Some stages used `h2` or a direct title/description stack that missed the shared hierarchy. Shared stage styles now cover both heading forms and direct stacks. Six-stage captures verify the result.
3. **Resolved P2: path-entry popover clipped in full view.** Horizontal overflow on the import toolbar clipped the mounted fallback. The toolbar now wraps; the final browser test opens the named disclosure and confirms the complete path textbox is inside the viewport.
4. **Resolved P2: loss of access to a utility or live status.** Independent review found a missing global glossary entry and training recovery status inside settings. The settings menu now opens the glossary, and active/recovering training status remains outside collapsed settings. Existing editors and effects remain mounted.
5. **Resolved P2: assistant and compact-width competition for space.** The shell container query moves the next-action panel below the work area as available width shrinks. At 1100 px with the assistant open and at 820 px, the recommended action remains visible and the page has no horizontal overflow.

## Required fidelity surfaces

| Surface | Assessment and deliberate production adaptation |
| --- | --- |
| Fonts and typography | Retains the application's Korean font stack and fallback. Compact 26 px stage headings, 24 px next-action heading, and 14–16 px main UI text create a consistent hierarchy. Wordmark weight/tracking is restrained; important copy wraps rather than being truncated. The generated reference has no authoritative font specification, so exact glyph identity is not claimed. |
| Spacing and layout | Warm continuous workspace, compact project/goal header, central photo/editor area and roughly 29% next-action column. Quiet dividers replace repeated framed cards. Data import, recovery and warnings consume more space than the mock because they are real required actions. Gallery scroll retains all photos; all six existing stages remain reachable through four work goals or full view. |
| Colors and tokens | Ivory and forest-green semantic workspace tokens follow the selected direction. Warning/error semantics remain distinct. The generated light adapter derives from the palette source; explicit light/dark/system preference is preserved. Scoped main-content axe checks found zero serious/critical violations in all six captured stages. This is not a complete accessibility certification. |
| Images and assets | Actual synthetic example thumbnails, natural aspect ratios, plain file/class captions and pagination. Low-resolution source samples remain visibly low-resolution when enlarged; this is not image degradation introduced by the UI. Real source images are never replaced by mock artwork. Existing Lucide utility icons are retained; no invented logo illustration replaces an approved asset. |
| Copy and content | Four plain work goals: 사진 준비 / AI 만들기 / 결과 확인 / 검사 사용. One truthful recommended navigation action; diagnostics, blockers and exact saved class names are preserved. The button says it opens the relevant screen, rather than claiming training or approval happened. Advanced terms are behind named tools/details, with glossary access. |

## Interaction and responsive evidence

- [Six-stage browser evidence](docs/verification/2026-11-14-novice-workspace-refactor.md): real isolated backend, 64 synthetic images, data/label/training/evaluation/flow/inspection navigation, settings, glossary, project modal and Escape, full-view path fallback.
- [Dark and assistant](docs/verification/assets/novice-workspace/workspace-dark-assistant.png), [compact layout](docs/verification/assets/novice-workspace/workspace-narrow.png), [native window](docs/verification/assets/novice-workspace/native-selected-workspace.png).
- Fresh renderer tests: 3240 passed, 2 skipped, 0 failed. Browser suites cover 16 unique cases; native source-window check passed. Existing operation guards are exercised in focused tests and affected browser journeys.

## Open questions and limits

- No blocking design question remains for this selected direction. Novice human usability testing and production task performance remain separate acceptance work.
- The native capture uses an empty isolated profile; it verifies shell, theme, navigation and modal behavior, not a six-stage native training cycle.
- No installed app replacement, running user web-server update, main merge, remote CI claim, external model request or public release is part of this QA result.
- Advanced panels still contain necessary technical controls. This pass changes hierarchy and discoverability, not all specialist terminology.

## Implementation checklist

- [x] Selected direction implemented across the six stages.
- [x] Existing operations, source data, guards and editor lifetimes retained.
- [x] Visual mismatches fixed and final reference/render comparison inspected.
- [x] Light, dark, assistant, compact, modal, path fallback and native states checked.
- [x] Independent review and historical evidence integrity checks completed.

## Follow-up polish

Use a short novice task study to check the four work-goal names and the discoverability of specialist tools. This is a future product-usability measurement, not a reason to report an unmeasured improvement in completion time.
