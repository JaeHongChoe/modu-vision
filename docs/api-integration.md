# SDK and automation integration

Use a saved, checksum-verified full flow package with its exact graph and model
files. A package execution result is distinct from model quality approval or
permission to activate an industrial service.

## Python, C++ and C# full flow SDK

The package carries its Python engine and native wrapper sources. Install the
package's runtime dependencies with the matching Python/OS/architecture. The
C++ library embeds CPython and starts an owned Python inference process; C#
calls that library through P/Invoke. These wrappers require Python and its
model dependencies. They do not provide independent native-only inference.

```python
from backend.engine.flow_package_runtime import Executor

executor = Executor('/absolute/package', device='cpu', deadline_ms=30000,
                    cpu_threads=1)
result = executor.execute({'image_path': '/absolute/image.png',
                           'image_id': 'inspection-001'})
```

Import from the delivered package's root. For multiple images, call the same
executor sequentially with each explicit image path and identity; the deadline
applies to each call, including its child initialization. Do not interpret an
exception, timeout or cancelled result as an OK inspection.

Build and execute the [delivered native examples](../native_runtime/README.md):

```sh
python native_runtime/build_native.py --output native-build
native-build/vision_predict /absolute/package /absolute/image.png 30000
dotnet build native_runtime/VisionRuntime.csproj -o native-build/csharp
dotnet native-build/csharp/VisionRuntime.dll /absolute/package /absolute/image.png 30000
```

Place the native library beside the C# client and make its matching libpython
discoverable. Native creation and package verification precede the per-image
budget. All SDKs execute the same graph and return its full JSON evidence.
Read redirected native output as UTF-8, including on Windows.

| Outcome | Python | C ABI execute/predict | C++ / C# |
| --- | --- | --- | --- |
| Completed graph | Full result dictionary | 0 and full JSON | Full JSON result |
| Invalid input, dependency or execution failure | Exception | 1 and error text | Exception |
| Deadline reached | `status: timeout`, REVIEW, owned termination receipt | 2 and same JSON | Same JSON |
| Confirmed cancellation | `status: cancelled`, REVIEW, `CANCELLED`, owned termination receipt | 3 and same JSON | Same JSON |

Request cancellation from another thread using `executor.cancel()` (Python or
C++), `executor.Cancel()` (C#), or `mv_cancel(handle)` (C). True/1 indicates a
request for the active call; false/0 indicates an idle handle. The returned
execution result confirms whether cancellation or completion won the race.
Cancellation does not poison the next call. A GAN generator uses the same
owned-process mechanism and publishes no candidate directory when cancelled;
completed synthetic candidates still require human review before adoption.

Do not destroy a C/C++ handle while Execute/Predict/Cancel is using it. C#
Dispose waits for its active calls while allowing concurrent cancellation.
Re-export older packages to deliver these cancellation methods.

## CLI and authenticated services

A delivered inspection package can run without the desktop UI:

```sh
python /absolute/package/run_flow.py --verify-only
python /absolute/package/run_flow.py --image /absolute/image.png --deadline-ms 30000 --output result.json
```

For a sequential batch, supply an explicit JSON manifest:

```json
{"schema":"FlowBatchInput/v1","images":[
  {"image_id":"part-001","image_path":"images/001.png"},
  {"image_id":"part-002","image_path":"images/002.png"}
]}
```

```sh
python /absolute/package/run_flow.py --batch /absolute/batch.json --deadline-ms 30000 --output batch-result.json
```

Paths resolve relative to the input manifest, or may be absolute. Require unique
nonempty image IDs, one to 1000 inputs and a manifest no larger than one MiB.
An optional `sha256` pins each expected input to 64 lowercase hexadecimal
characters. Unknown fields and duplicate keys/IDs fail before execution.
`FlowBatchResult/v1` retains input/package manifest hashes, per-image hashes,
ordered full graph results and summary counts. A missing, unreadable or changed
image becomes REVIEW/error; other inputs continue. A result whose image bytes
change during execution is discarded. Keep inputs stable for the entire batch.
Each image has its own deadline. Exit0 means no execution error or timeout;
exit2 indicates an image error, and exit3 indicates timeout/cancellation when
there is no image error. Inspect each verdict, including REVIEW, before use.
Ctrl+C aborts the batch and reaps its owned active inference process; an
interrupted batch has no completed batch receipt. Re-export older packages to
deliver this command.

The isolated service entry point is `python -m
backend.engine.inspection_service --package /absolute/package --state-dir
/absolute/owned-state`. Supply the existing `VISION_INSPECTION_TOKEN` securely;
the service defaults to loopback. Its versioned `/v1/jobs/upload` accepts
images, `/v1/jobs/{job_id}` returns durable status and `/v1/results/export`
returns retained results. Requests require `X-Vision-Token`. Queued, running,
delivery_pending and device acknowledgment are separate states. Capacity
limits return backpressure; an HTTP submission is not an inspection result.
See the [Node client](../examples/inspection-service-client.mjs) and
[C# HTTP client](../examples/InspectionServiceClient.cs).

The desktop backend's authenticated training CLI/REST engine is documented in
[training-engine-cli-rest.md](training-engine-cli-rest.md), including explicit
source/output paths, background status, cancellation and verified downloads.
Shared-server requests retain project roles; a local CLI does not bypass them.
Use each service's generated OpenAPI schemas for the exact deployed version.

## Execution evidence

The manual Windows and Linux native SDK workflows compile and execute Python,
C++ and C# full-graph parity, deadlines, checksum refusal, cancellation and
handle reuse. Required C# skips fail these gates. They retain exact source,
toolchains, JUnit and compiled-client artifacts. See the
[dated verification record](verification/2026-10-04-remaining-integration-evidence.md)
for observed results and failures. These gates do not establish Windows11
installation, signed distribution, physical-device behavior or model quality.
