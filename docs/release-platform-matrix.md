# Native release and recovery acceptance

Every desktop package contains a frozen `backend_bin` directory. Building from
Python sources remains a development workflow. Packaged startup requires the
matching executable and its checksum-bound inventory; it does not install Python
packages into the operator's machine.

| Target | Native build | Execution validation | Publisher and delivery prerequisites |
| --- | --- | --- | --- |
| macOS arm64 | Run `scripts/build_backend_binary.py` on arm64 macOS, then the macOS desktop builder. | Frozen dependency imports, two isolated backend launches, optional known-image inspection and service restart. | Publisher code signature for the shell and frozen backend, notarization, Gatekeeper, delivered installer and physical equipment acceptance. Ad-hoc signatures are unsigned publisher status. |
| Windows x64 | Run the Python builder on Windows x64, then `electron-builder --win --config build/electron-builder.yml`. | Same frozen launch and restart validator on Windows; Authenticode check uses PowerShell. | Trusted matching publisher signatures for the executable and installer, actual NSIS install/uninstall in an isolated target, device drivers and physical equipment acceptance. |
| Linux x64 | Run the Python builder on Linux x64, then `electron-builder --linux --config build/electron-builder.yml`. | Same executable launch and restart validator on Linux. | AppImage/FUSE or supported `.deb` host, configured publisher signature verification, isolated install/uninstall, user-session startup and physical equipment acceptance. Without a signature verifier the artifact is unsigned. |

Cross-platform packaging cannot reuse another platform's runtime receipt. The
builder hook rejects a different OS/architecture, missing executable acceptance,
changed backend sources and changed frozen library files. Actual target signing,
installer acceptance and physical execution require their own evidence.

## Build and validate

Use a project environment with the declared `requirements.txt` versions and
PyInstaller. Install into that environment, then invoke its Python explicitly:

```sh
.venv/bin/python scripts/build_backend_binary.py
npm run build
npx electron-builder --mac --config build/electron-builder.yml
```

On Windows use `.venv\Scripts\python.exe` for the Python command. Choose `--win`
or `--linux` on those actual hosts. Use `--output PATH` to keep an experimental
backend build isolated from the packaging input `dist-backend`.

Build inputs are copied into an immutable source snapshot before compilation.
`backend-release.json` binds the exact executable, library/resource checksums,
source and dependency versions, platform and build identity. A required module
below the declared version blocks the build. `backend-acceptance.json` records
the actual frozen import, health and restart results. A failed or timed-out check
stays failed; a compiler exit alone is not execution acceptance.

To execute a known image through a package and the restarted inspection service:

```sh
.venv/bin/python scripts/release_backend_acceptance.py \
  --backend-dir dist-backend/vision_ai_backend --output release/known-image-acceptance.json \
  --package /absolute/path/to/package --image /absolute/path/to/image.png --device cpu
```

This uses temporary state, loopback ports and only the processes it starts. It
does not register an OS service. The report binds the package manifest, source
image, actual device and runtime build. Execution does not approve model quality.

Run final platform diagnostics against the unpacked application's backend and
the actual delivered application/installer:

```sh
node scripts/release-readiness.cjs --backend-dir /absolute/path/to/backend_bin \
  --app /absolute/path/to/application --output release/final-readiness.json
```

Sign native components before accepting their bytes. A later signature or any
other byte change invalidates the frozen inventory and acceptance. Verify and
rebuild the inventory/acceptance rather than reusing a pre-signing receipt. This
repository does not supply a signing identity or an update-channel server.

## Offline prerequisites

Python and required inference dependencies are bundled. Exported inspection
packages must carry their verified model weights. Pretrained DINO/YOLO weights
for uncached training are not bundled; provision an allowed weight cache before
offline training. Optional OCR foundation models, DICOM and OpenVINO/quantization
support are listed with installed versions or explicit unavailable status. CUDA
drivers and device SDK/permission requirements remain target prerequisites.

## Cohort evidence and frozen exported runners

Managed deployment and a trusted approved-release policy require a passed
`flow_parity_v1` receipt with `scope=cohort`, 2–64 completed images, exact image
hashes, graph/model/manifest identity and the actual selected device. Single-image
compatibility evidence cannot qualify a release. The separately trusted policy
binds `device` and `parity_receipt_sha256`; the staged package retains that exact
receipt. Receipt changes are rejected during apply, recovery and startup.

OpenVINO CPU precision releases use the separate
`measured_precision_cohort_v1` contract and `runtime_acceptance_sha256`. Approval
requires 2–64 distinct heldout val/test paths, unchanged original image and saved
split hashes, complete saved reference and actual OpenVINO CPU flow outputs,
finite conversion drift within the reviewer's explicit bound, and an explicit
reviewer, reason and holdout review. Disagreements remain in the acceptance
record. This contract does not create a parity pass. It requires the native
OpenVINO dependency and measured execution on the target; unavailable optional
runtime dependencies remain prerequisites.

Authenticated field delivery transports the separately trusted release policy
in `X-Release-Policy`, with the exact manifest, approved revisions, device and
receipt hash. The archive carries the external parity receipt or the
manifest-listed measured precision acceptance. The field agent verifies these
bindings at staging and application; it does not derive approval from uploaded
model metadata. Existing receivers must support this request contract before
delivery.

In a frozen application, parity starts a dedicated process:

```sh
vision_ai_backend --flow-package-runner --package /absolute/package \
  --manifest-sha256 PACKAGE_SHA256 --image /absolute/image.png \
  --device cpu --output /absolute/result.json
```

The dispatcher verifies every manifest-listed file before loading the package's
own exported backend namespace and runner. It uses the frozen third-party
libraries. It does not rerun the studio engine as the purported exported side.
The deadline child equivalent is `--flow-package-worker` with `--package`,
`--manifest-sha256`, `--request` and `--output`. Both outputs must be outside the
immutable package. These receipts prove the observed runtime/device; another
machine still needs its own target execution.

## Interrupted operations

The existing deployment ledger records update intent before runtime mutation.
Activation history and the active pointer commit only after matching manifest and
device acknowledgment. An interrupted switch restores the last committed
release on explicit service start. Failed restoration stays pending review and
does not overwrite the accepted pointer. A first interrupted deployment has no
accepted prior runtime and requires explicit reapplication.

Native startup installation records the project label, descriptor hash, package
and runtime build before registration. Readback exposes an interrupted operation;
explicit retry reconciles the owned registration without launching another
service. Registration failure restarts a previously running independent service.
Descriptors and deployment history remain available for diagnosis. Live service
readback carries the runtime build captured by that process; manager build
identity is reported separately, so replacing the desktop cannot relabel an
already running service as the new build.

Manual desktop downloads journal installed/candidate checksums and the release
manifest. Restart removes only the interrupted operation's own partial file.
Downloaded bytes require publisher verification; a manual handoff record never
means the new application was installed. Installed version, actual launch,
known-image execution and physical acceptance are checked separately.
