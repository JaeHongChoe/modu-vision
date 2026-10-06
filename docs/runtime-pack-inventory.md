# Optional runtime inventory

CPU installation remains the default. `scripts/runtime_pack.py` inventories an
explicit local NVIDIA, OCR, DICOM or OpenVINO pack without installation,
importing its code, fetching dependencies or changing the environment.

The reviewed descriptor supplies ID/version, kind, platform/architecture,
credential-free HTTPS source, license identifier, NVIDIA minimum driver (null
for other kinds), exact worker/runtime protocol versions and every relative
file. The inventory records each file's byte length and SHA-256 and total bytes.
Verification requires an independently reviewed manifest SHA-256. Missing or
extra files, links, replacement/growth, unsafe names, case collisions, target
or protocol mismatch and inadequate declared driver are refusals.

Run `python scripts/runtime_pack.py --help` for inventory/verify subcommands.
Outputs are new exclusive files. Verification reports
`inventory_verified_runtime_unqualified`: it does not establish a signature,
successful import, actual target inference or model precision. The free-space
check reserves three times the payload as a staging estimate, not an installer
capacity guarantee. Bind an actual distributed pack into the signed release
inventory separately, then run the production worker preflight and a frozen
cohort on the exact device/runtime. Publish measured precision/drift before
qualifying ONNX, OpenVINO or quantized variants. iGPU/NPU/Jetson/MIG support
cannot be inferred from a CPU or another GPU receipt.

No optional pack is fabricated to make an unavailable provider appear ready.
Reviewed distributable pack bytes, license notices and target execution remain
S6-03 prerequisites.
