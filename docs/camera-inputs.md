# Camera and video input evidence

The operator input form saves a USB device index or RTSP/RTSPS address and an
opaque camera ID. Saving does not open a camera or restart the service. Apply the
configuration with an explicit service stop/start and check actual adapter health.
USB indices default to `usb:<index>`; network cameras require a separate ID.
IDs contain up to100 ASCII letters, digits, underscores, periods, colons or
hyphens. Do not use an address as the identity.

The saved ID is passed through the managed worker's `--camera-id` argument.
Older network-camera configurations without a valid ID require explicit
reconfiguration; reading them does not rewrite the saved file or open a device.
Manual/folder configuration clears the camera fields. URI authentication and
target security qualification remain separate operating requirements.

The OpenCV adapter records admitted frames with camera ID, read session, sequence,
host read-completion epoch/monotonic time, clock discontinuity and observed
service drop/read/reconnect counts. Host timestamps are not sensor timestamps.
Unseen sensor/network drops cannot be inferred from these counters. Buffer bytes
are owned at admission. A capture-group policy requires an explicit trigger
provider and part/trigger/view identity; missing or changed trigger identity
retains REVIEW, and capacity refusal does not stage an accepted image.

`CameraAdapter` and `CameraAdapterFactory` define trusted in-process SDK bindings.
Adapters must return bounded uint8 BGR buffers and implement open/read/release.
No vendor SDK is downloaded or dynamically loaded. `SimulatedCamera` supplies
finite owned frames and explicit exhaustion; it is not a physical device.

Software checks cover actual owned video decoding through OpenCV and actual CPU
package inference using deterministic untrained segmentation weights, durable
frame provenance/EOF state, trigger/drop/reconnect/failure contracts and the
SDK/simulator interface. Browser/macOS Electron checks save/reopen identity,
reject invalid IDs, preserve the USB default and explicitly clear the setting.
Those form checks do not open the configured USB or RTSP device. Actual physical
USB/RTSP, industrial SDK, hardware trigger and sensor-clock accuracy need their
own target records. No model-quality or field-readiness approval follows from
these functional fixtures.
