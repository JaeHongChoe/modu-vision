# OCR and rotation native control evidence

The retained clean native executions were read back against their exact test
source, manifest and screenshot hashes. Twenty individually reviewed actions
add31 verified scenarios to the action inventory. Earlier evidence is retained:
137 curated actions,248 verified scenarios and711 pending scenarios. These
scenario counts are an expanding control inventory, not remaining parent jobs.
Whole-feature and human acceptance remain unapproved.

## OCR

The native OCR workbench saves/reopens exact entered Unicode and whitespace,
original image hashes and independent splits. It refuses heldout characters
absent from the training alphabet. A real120-epoch local CPU recognizer completed
on synthetic Korean/digit glyphs; two heldout images had CER0/WER0. Horizontal
multiline input returned two ordered lines and original96x84 pixel regions;
blank input returned no text or regions. This is horizontal region proposal
plus recognition, not learned scene-text detection or vertical-text support.

The same model, prepared input and text settings reopen. Explicit allowed-value,
regex and length rules retained their rejection results separately from the
recognized text. Native model binding handed off the exact candidate; separate
actual API flow and independent candidate-package processes preserved text,
regions, rules and the NG verdict. Package creation used a helper path, so this
does not claim GUI export, signed publication or physical deployment.

## Learned direction and fitted boxes

Native rotation preparation preserved original8/4/4 split hashes. The actual
20-epoch CPU job completed with decreasing recorded loss. Four synthetic heldout
images measured circular MAE9.9344 degrees and a within10-degree fraction of0.5;
those measurements are not representative production quality. The selected
original aligned through the learned candidate, native export produced a
TorchScript candidate and a separate process matched the exact output pixels.
Reopen and explicit flow-model binding retained the selected best model. This
rotation case did not execute the whole flow.

Three native box-fitting modes used independently calculated delivered mouse
coordinates. Class, split, original SHA, fitted box/corners and independent
315/0/180-degree directions survived save/reopen. Axial fitted angles stayed0
degrees. This content case submitted no training and does not enable separate
direction prediction in YOLO OBB.

Receipt identities are bound per action in `docs/service-action-evidence.json`.
The source execution is0eadf8c9ee87e894597286fdc85bed625771e132. The complete
native batch included44 passes and one separate obsolete OBB response-observer
failure; only the three exact passed OCR/fitting/rotation cases are used here.
The OBB observer repair and later successful rerun have their own6246 evidence.
Root review is recorded; independent review and model quality approval are not.

Parent accounting remains66 software verified/16 pending, including one waived
Windows actual-QA item. The72-hour duration gate continues independently.
