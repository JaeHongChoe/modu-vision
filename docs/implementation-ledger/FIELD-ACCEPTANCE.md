# Target and physical-device acceptance

Status: actual-device acceptance pending. Simulator contracts are qualified
separately from hardware use.

| Boundary | Required actual receipt |
| --- | --- |
| Worker | OS, runtime, explicit CPU/CUDA device and model/package hashes; real execution and owned cleanup |
| Camera/video | Device/driver, acquisition, frame identity, reconnect, dropped frames and lighting/geometry limits |
| PLC | Actual protocol/device, timeout, duplicate request, safe output and acknowledged result |
| MES | Actual endpoint/schema, authentication, duplicate delivery, offline outbox and acknowledgment |
| Independent service | Dedicated account, startup/reboot, readiness, project access, crash recovery and result publication |
| Release | Target-side package identity, approved cohort parity, active revision, rollback and evidence readback |

For each receipt retain start/end times, source/build identity, commands, logs,
input/output manifest, failure cases and resource ownership. Never operate another
job or device merely to obtain a passing result. Preserve explicit REVIEW on
missing execution or uncertain recovery.

Windows actual tests are excluded from the current development request by the
user. This scope exclusion does not establish Windows install, Session0, ACL,
camera or CUDA support. Record it as an exclusion alongside future target evidence.
