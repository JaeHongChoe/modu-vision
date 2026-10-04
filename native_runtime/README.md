# Native full flow SDK

Build on the target using the Python interpreter whose torch/runtime dependencies
are installed. Python development headers and a shared libpython are required.

```
python native_runtime/build_native.py --output native-build
native-build/vision_predict /absolute/package /absolute/image.png 30000
```

The C++ header exposes Predictor/Executor. The C ABI embeds CPython, verifies the
package and starts an owned inference process using the configured interpreter.
It executes all saved DAG nodes and model families; the complete JSON evidence is
the same as Python `Predictor.predict()` and `Executor.execute()`.
`deadline_ms` includes child initialization, reading pixels and all model nodes.
Timeout returns REVIEW and terminates the owned process group; exit code is 3.
Native creation/dependency initialization and package integrity verification are
preconditions performed before the per-image inference budget.

C# uses P/Invoke against this same native library, without an HTTP service:

```
dotnet build native_runtime/VisionRuntime.csproj -o native-build/csharp
dotnet native-build/csharp/VisionRuntime.dll /absolute/package /absolute/image.png
```

Place the built native library in the C# output directory (and libpython in the
platform library search path). Linux/macOS builds add a libpython rpath. Windows
requires CMake/MSVC and Python DLL discovery. Deploy matching Python/native/OS
architecture and dependency versions. Cross compilation does not prove target
hardware acceptance. Each handle serializes calls; do not release a handle while
another thread is using it. Returned C strings must be released with `mv_free`.

Windows builds copy the declared Release DLL, import library and three executable
clients into the requested output directory. CMake output missing any of these
files is a failed build. The manual `Windows native SDK execution` workflow
compiles and executes the delivered C++ and C# SDKs on hosted Windows Server2025
x64, checks whole-DAG parity, deadlines and tampered-code refusal, and retains
source/toolchain/JUnit evidence. It does not install the desktop application,
register services, publish binaries or establish Windows11/device acceptance.

Cancellation is available as Python `Predictor.cancel()` / `Executor.cancel()`,
C++ `executor.cancel()`, C# `executor.Cancel()`, and C ABI `mv_cancel(handle)`.
Call it from another thread while an inference is running. It returns true (C:
1) when cancellation was requested for the active call, false (C: 0) when idle.
An accepted request is confirmed by the returned JSON `status: cancelled`,
`final_verdict: REVIEW`, `rejection_reason: CANCELLED` and the owned process
termination receipt. A completed result may win a late cancellation race.
Each next execution uses a fresh cancellation event, including after errors.
C ABI execution status is 0 for a result, 1 for an error, 2 for timeout and 3
for cancellation; `mv_cancel` returns -1 for invalid/unsupported handles.
C# holds the native handle until concurrent Execute/Cancel calls return;
C/C++ callers must not release or destroy it while any call is using it.

The delivered examples cancel a running inspection and then reuse its handle:

```
native-build/vision_cancel_demo /absolute/package /absolute/image.png
dotnet native-build/csharp/VisionRuntime.dll /absolute/package --cancel-demo /absolute/image.png
```

They return both the cancelled result and the following full DAG result as
UTF-8 JSON. Read redirected SDK stdout as UTF-8 explicitly on Windows; the
console's default code page may not decode Korean graph labels. GAN executor
cancellation uses the same owned process receipt and publishes no candidate
directory when execution returns cancelled. Old exported runtimes must be
exported again to gain these cancellation methods.

Inspection dispatch covers classification, patch classification, segmentation,
detection, anomaly, OCR, rotated detection, learned rotation and enhancement
through the saved graph, including ROI, branches and calibrated measurements.
Python and both native wrappers expose `Predictor` and `Executor` calls.

GAN packages use `vision_execute PACKAGE REQUEST.json [DEADLINE_MS]` or C#
`VisionRuntime.dll PACKAGE --execute REQUEST.json [DEADLINE_MS]`. A request needs
`output_dir` (a fresh directory), optional integer `count`/`seed`, and optional
`source_image_path`, `source_sha256` and explicit native-coordinate `regions`.
This calls the real trained generator and retains source/seed/hash provenance.
It produces `synthetic_unreviewed` candidates; explicit human review is required
before training adoption. Timeout publishes no candidate directory.

OpenVINO conversion candidates retain their original PyTorch checkpoints and
include compiled IR for every connected model. CPU, GPU/iGPU and NPU names are
checked against actual OpenVINO devices; an unavailable target never falls back
to CPU. Approved precision releases fix the reviewed device. CUDA/Jetson uses
the same saved graph and weights with a matching vendor PyTorch/runtime; target
preflight and execution are required before claiming hardware acceptance.
