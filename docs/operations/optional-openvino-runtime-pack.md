# Separate offline OpenVINO runtime pack

The qualified control keeps the base backend and an optional OpenVINO runtime
separate. `scripts/runtime_pack.py` inventories and copies an inert package; it
does not import wheels, install Python dependencies, download anything, activate
a service or grant execution/quality approval.

## Explicit supplied inventory

The Linux x64 control contains the exact CPython 3.11 wheel set:

| Dependency | Version |
|---|---|
| OpenVINO | 2026.4.1 |
| NumPy | 2.2.6 |
| packaging | 24.2 |
| OpenVINO telemetry | 2025.2.0 |

Each original wheel is pinned to the SHA-256 from its official PyPI release
metadata. The payload contains a `--require-hashes` offline lock, upstream
bindings including the Python ABI, and original license/notice bytes extracted
from those exact wheels. The descriptor uses
`LicenseRef-OpenVINO-RuntimePack`; it does not characterize all bundled third
party/native libraries as Apache licensed or approved for public distribution.
Their legal review remains separate.

The independently retained inventory SHA is supplied explicitly to `verify` or
`install`; it must not be inferred from an untrusted incoming file. Destination
OS, architecture, worker/runtime protocol and available staging storage must
match. NVIDIA driver verification belongs to a NVIDIA pack, not this CPU pack.
A matching inventory permits only inert installation. It does not mean that the
Python ABI, accelerator/provider or a representative model has been qualified.

```sh
python scripts/runtime_pack.py install \
  --root /path/to/exact-supplied-payload \
  --document /path/to/reviewed-inventory.json \
  --expected-sha256 <independently-retained-inventory-sha256> \
  --platform linux --arch x64 --worker-protocol 1 --runtime-protocol 1 \
  --store /path/to/existing-owned-runtime-store \
  --output /path/to/new-installation-receipt.json
```

Installation atomically publishes a private inventory, receipt and exact
payload. A repeated command verifies the same bytes; it neither repairs a
changed installation nor overwrites a competing directory. The receipt remains
`installed_inactive_runtime_unqualified` with activation, signature and execution
flags false.

## Runtime execution qualification

An explicit owner may qualify the installed wheels in a separate, disposable
Python dependency environment. The recorded Linux control installs with
`--no-index --no-deps --ignore-installed --require-hashes` from the installed
payload into an owned disposable venv using the base Torch dependencies. It
leaves the base image/service and installed pack unchanged. The explicit
`VISION_OPENVINO_PYTHON` names that venv executable, so conversion and full-flow
inference children use its installed dependencies while retaining their verified
source/package import boundaries. A transient parent-only `PYTHONPATH` overlay
does not qualify those child processes.
It observes the actual CPython ABI, OpenVINO version and available devices;
requesting an unavailable `NPU.999` is refused rather than sent to CPU.

The container has no network or GPU access, a read-only root, no published ports,
all capabilities dropped and `no-new-privileges`. The runtime's telemetry cannot
send data through that network-disabled environment. The owned container is
removed after the run. Original payload hashes and replayed installation
receipts are checked after execution.

Controlled FP32/FP16 tensors and a tiny full-flow reference/heldout image exercise
real conversion, saved precision metrics, provider inference and the complete
flow package. They do not approve industrial model accuracy, representative
heldout quality, a physical camera/PLC/MES, NPU/iGPU/Jetson/MIG/Windows, INT8/NNCF,
a real publisher or a release. Exact source, inventory, wheel, log and execution
receipt hashes identify each qualification separately.
