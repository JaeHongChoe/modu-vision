# Optional runtime inventory and inactive installation

CPU installation remains the default. `scripts/runtime_pack.py` inventories an
explicit local NVIDIA, OCR, DICOM or OpenVINO pack. Its separate `install`
command copies a reviewed pack into an explicit local store. It does not import
pack code, fetch dependencies, change the environment or activate a runtime.

The reviewed descriptor supplies ID/version, kind, platform/architecture,
credential-free HTTPS source, license identifier, NVIDIA minimum driver (null
for other kinds), exact worker/runtime protocol versions and every relative
file. The inventory records each file's byte length and SHA-256 and total bytes.
Verification requires an independently reviewed manifest SHA-256. Missing or
extra files, links, replacement/growth, unsafe names, case collisions, target
or protocol mismatch and inadequate declared driver are refusals.

Run `python scripts/runtime_pack.py --help` for inventory/verify/install commands.
Outputs are new exclusive files. Verification reports
`inventory_verified_runtime_unqualified`: it does not establish a signature,
successful import, actual target inference or model precision. The free-space
check reserves three times the payload as a staging estimate, not an installer
capacity guarantee.

## Inactive installation

Supply an existing unlinked `--store`, disjoint from `--root`, plus the same
`--document`, independently reviewed `--expected-sha256`, target `--platform`,
`--arch`, `--worker-protocol`, `--runtime-protocol` and (for NVIDIA) driver version
used for verification. The required `--output` is a new receipt file.

The installer checks actual store free space, copies only the inventoried files
with bounded reads and source identity/hash checks, flushes the files, verifies
the complete copy and rechecks the source before publishing. The final directory
is named by pack ID, version and full inventory SHA-256. Publication refuses to
replace an existing directory, including an empty racing destination. Unsupported
atomic no-replace platforms/filesystems fail rather than falling back to an
overwriting rename. Repeat installation verifies the existing records and payload;
different, linked or extra content is never repaired automatically.

The receipt state is `installed_inactive_runtime_unqualified`: installation is
true, activation, signature and execution verification are false. Interrupted
copying leaves no selected runtime. No driver/service installation, extraction,
environment change or activation pointer is performed. Local copy, disk-full,
source-change, tamper, target-race and repeat-install controls are covered by
`backend/tests/test_runtime_pack_installation.py`; they are not a distributed
provider/hardware qualification.

Bind an actual distributed pack into the signed release
inventory separately, then run the production worker preflight and a frozen
cohort on the exact device/runtime. Publish measured precision/drift before
qualifying ONNX, OpenVINO or quantized variants. iGPU/NPU/Jetson/MIG support
cannot be inferred from a CPU or another GPU receipt.

No optional pack is fabricated to make an unavailable provider appear ready.
Reviewed distributable pack bytes, license notices and target execution remain
S6-03 prerequisites.
