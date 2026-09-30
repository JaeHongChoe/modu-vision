# Model and portable runtime completion design

## Accepted intent
Finish the previously listed implementation gaps, keep the native industrial inspection flow usable, and clearly expose all implemented model families. The user approved continuation and normal main publication; do not repeat approval prompts. Functionality and flow are the priority.

## Design
0. User steering: default image classification, patch classification and segmentation to pretrained DINOv3 ViT-S/16, with ViT-B/16 selectable. Default normal object detection to pretrained YOLO26n, with YOLO26s selectable. Persist architecture and source weight SHA-256; task heads train from current labels. Saved legacy models reconstruct offline from their original architecture. Rotated detection retains its explicitly named specialist architecture. Missing pretrained weights or dependencies fail with actionable errors, without random initialization substituted silently.
1. Extend verified parent-model training to compatible detection and patch models and specialist OCR, rotated detection, GAN, and enhancement families. Statistical anomaly models explicitly refit their statistics using a verified parent feature extractor where technically supported; never claim optimizer continuation for statistical models. Parent identity, source scope, class/architecture signature and checkpoint hash remain strict. Fresh training remains possible. Preserve lineage in the new candidate and receipts, never mutate the parent.
2. Add a portable CPU Edge deployment profile to complete-flow exports: declare target OS/architecture and Python/runtime requirements, include executable install/preflight/launch paths and verify package integrity before execution. This is generic CPU deployment, with no vendor SDK, quantization or fabricated hardware certification. Keep existing CPU/CUDA/MPS exports compatible.
3. Expose a code-grounded model family catalog in Stage 3: classification, detection, segmentation, anomaly, patch classification, OCR, rotated detection, defect generation, enhancement. Show actual available architectures, supported stages/devices, parent-training semantics and missing prerequisites. Do not advertise unimplemented architectures or experimental quality as approved. Connect specialist parent selectors to their own workbenches.
4. Verify the changes with meaningful regressions, the actual supplied images, a native build/restart, portable CPU package execution, and current Git identity. OCR real transcription and field equipment acceptance require genuine inputs; fixture coverage must remain labelled as fixtures.

## Constraints
- Python 3.10 compatible; existing PyTorch/React/FastAPI dependencies. No proprietary company names, data, credentials, device addresses or weights in public files.
- Original dataset is read-only. Use owned QA copies and isolated project model stores. No change to unrelated apps, processes or GPUs.
- CPU/Linux/Windows/macOS target declarations are not proof of execution on each OS or Edge board. No silent device fallback.
- Specialist warm starts use their current scoped resolver/provenance contracts; stale or corrupt models must not become eligible.
- Current user request and prior approval authorize execution and normal main push; no forced history replacement.

## Completion boundaries
Implemented family catalog and strict continuation paths; runnable CPU Edge bundle and preflight; fresh tests/native proof; accurate status matrix. Real OCR truth, external equipment, untested OS/hardware and quality approval remain explicit evidence limitations.
