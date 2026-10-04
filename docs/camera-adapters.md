# Camera adapter contract

The inspection service uses OpenCV USB/RTSP capture by default. A trusted Python
integration can pass `CameraAdapterFactory('sdk', open_camera)` to
`create_service_app(..., camera_source=source, camera_id='line-A', camera_adapter=factory)`.
The opaque camera ID is required for SDK and simulator adapters. Keep credentials
and connection addresses out of IDs and returned status.

`open_camera(source)` returns an object with these methods:

- `isOpened() -> bool`
- `read() -> tuple[bool, numpy.ndarray | None]`
- `release() -> None`

A successful read supplies uint8 BGR with shape `(height, width, 3)`, at most 64 MiB.
The adapter converts vendor formats and keeps the buffer stable until the next
read; the service copies it before admission. False means disconnected/end of
stream. Open, read and release must return within the integration's bounded
interval. The service cannot forcibly recover an in-process vendor SDK hang.
No module name, download, SDK installation or arbitrary plugin path is loaded by
this contract.

The existing service records opaque camera/session IDs, sequence, host epoch and
monotonic read-completion times, observed admission drops, read/connection errors
and reconnections. Sensor exposure time, network packet loss and hardware timing
are not inferred. A configured trigger provider supplies part/trigger/view IDs;
missing or uncertain correlation receives REVIEW. Capacity rejection still
consumes the observed frame/trigger and records its drop.

## Deterministic simulator

```python
import numpy as np
from backend.engine.camera_adapters import CameraAdapterFactory, SimulatedCamera
from backend.engine.inspection_service import create_service_app

capture = SimulatedCamera([np.zeros((32, 32, 3), dtype=np.uint8)])
factory = CameraAdapterFactory('simulator', lambda source: capture)
app = create_service_app(
    package_dir, owned_state_dir, token=existing_test_token,
    camera_source='simulated-line', camera_id='line-A',
    camera_adapter=factory, camera_frame_interval=0.1,
)
```

Supply a verified package, owned state directory and existing test token. The
simulator owns the supplied pixels, returns independent buffers and disconnects
at exhaustion. Release is idempotent and does not replay frames. It accepts 1..512
frames with a total buffer limit of 256 MiB. A real reconnecting SDK factory should
return a fresh bounded adapter instance when called again.

`GET /v1/adapters` reports `camera_adapter_kind` as `opencv`, `sdk` or `simulator`
and `camera_hardware_verified: false`. Tests exercise actual service admission,
inspection, restart readback, BGR/RGB pixel preservation and malformed/close
failures. Physical devices, vendor SDK timing, process isolation for hung SDKs
and target OS/device acceptance require their own evidence.
