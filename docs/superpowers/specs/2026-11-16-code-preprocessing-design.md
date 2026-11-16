# Programmable image preparation

The user wants to type preprocessing code, or ask the embedded assistant to write
it, including FFT removal of periodic ultrasound noise. Extend the current saved
input recipe and preparation wizard in the existing isolated feature branch.
This is an implementation choice within the requested feature, not a request to
send images or execute a real provider call.

## Approach

Use a versioned numeric language with Python assignment/expression syntax. A
small AST interpreter dispatches explicitly supported numeric functions; it never
executes Python bytecode, `eval`, `exec`, imports, attribute access, loops, file or
network operations. An unrestricted Python subprocess would introduce arbitrary
system access and substantial cross-platform isolation requirements. A fixed FFT
button alone would not provide the requested editable code. The restricted
numeric program supplies real editable algorithms with a bounded vocabulary.
It is not an operating-system sandbox or general Python environment.

## Executable contract

- Recipe v1 gains `{op: 'code', language: 'modu-numeric-v1', code: string}`.
- `image` is a private float RGB array, range 0..255. Assign `result` with the
  same height, width and three channels. Finite real output is rounded/clipped
  to uint8. The image geometry and segmentation labels remain unchanged.
- Initially retain classification/segmentation support; do not widen the task
  allowlist or silently apply incompatible transforms to boxes/other models.
- Code is at most 8192 UTF-8 bytes and 32 lines/statements, with bounded AST
  size/depth, variable count, numeric constants, image pixels and operation
  allocation/work. Reject unsupported syntax/functions before running anything.
  Bounds, rather than a claimed hard real-time deadline, constrain native work.
- FFT functions use spatial axes (0, 1), never RGB channels. A symmetric smooth
  notch helper uses normalized cycles per pixel and preserves DC. Its example
  frequency is a template, not an ultrasound noise calibration or quality claim.
- Hash the exact canonical code in the recipe. Include the numeric implementation
  version in code-containing saved bindings, preserving all old bindings.
- Preview, training, evaluation, flow/inference and exported inspectors use the
  same module and contract. Export the interpreter along with the recipe engine.
- Originals are never overwritten; no new dependency, environment mutation,
  actual training, paid/provider request or outside image transfer is needed.

## User flow

In Photo preparation add a Code block. Show a short explanation and FFT example;
put the monospaced editor and function reference behind a clear disclosure.
An ordinary-language request opens the existing assistant with a scoped draft;
it never sends automatically or discards an existing draft. Reuse
`configure_input_recipe` so the assistant can write code, execute a real local
preview, verify recipe/source hash and size, then save the exact scoped recipe.
An editor change invalidates preview qualification. An assistant save does not
overwrite a dirty editor: retain the current conflict/reload behavior.

Full-preview success proves executability on the selected sample, not defect
preservation or field performance. The preview includes original/transformed
images and aligned masks. Changing the recipe requires a new model/evaluation;
old models remain bound to their old code. Require a resize/crop before a code
block when the original exceeds the numeric runtime pixel bound.

## Verification

Exercise deterministic FFT on synthetic periodic RGB noise (signal/DC retained,
target frequency suppressed), invalid/nonfinite results, forbidden syntax,
resource bounds, original/mask immutability, old/new binding compatibility,
checkpoint mismatches and exported standalone execution. Test assistant schema,
mock tool preview/save, context fences, draft handoff and dirty preview retirement.
Run a real owned-renderer/local-backend UI flow with no provider/model execution.
Preserve current CI history; never re-dispatch an existing full or deploy to the
user's live app/web while the new head remains unqualified.
