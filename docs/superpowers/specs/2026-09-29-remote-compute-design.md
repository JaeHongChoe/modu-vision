# Remote Compute Design

## Intent and scope

Keep the desktop application and its dataset, labeling, and model history on the user's computer. Let the user select a saved SSH server for expensive training and inspection work. A configured Linux host can be the first profile; other hosts can be added with their own SSH address, runtime, and isolated workspace. Local computation remains available.

The user chooses a compute location before starting a job. Every job retains its original location even if the selection changes. An interrupted network connection is reported as **status unknown / reconnect**; it never silently restarts locally, marks a remote job cancelled, or starts a duplicate job.

## Architecture

The Electron renderer continues to call the local token-protected FastAPI daemon. A compute-profile service in that daemon validates and persists SSH profiles. An SSH transport uses the user's existing OpenSSH key/agent and known-hosts verification, without storing a password or exposing a remote HTTP API. A versioned Python worker runs under the selected server's configured Python or Docker runtime in an app-owned workspace. It writes durable status, progress, cancellation, and artifact manifests. The local daemon owns the job journal and maps remote results back to local model and dataset identities.

The transport has operations `probe`, `upload`, `run`, `status`, `cancel`, and `download`. The worker protocol has a version, job ID, operation, task, configuration, portable input manifest digest, and expected artifact list. The worker can support `train`, `evaluate`, `flowchart_run`, `benchmark`, and `export`; each operation is attributable to a specific server and job. A profile may select a direct Python runtime or a Docker image. Each host gets an isolated runtime and app-owned workspace; existing services, containers, and project environments are not modified.

## Data and result flow

Stage 1 and Stage 2 always operate on local files. For remote training, the app first validates the same split and labels used by local training, then creates a task-ready immutable snapshot. A flat LabelMe folder is prepared locally with the existing converter, so its Studio annotation overrides and saved split are included. Structured datasets are copied with safe relative paths and symlinks resolved to content. An SHA-256 manifest records every transferred file and byte count. The worker rejects missing, extra, or mismatched input and stages data only under its own run directory.

The remote worker trains on the snapshot and writes the checkpoint, metadata, status, and output manifest. The local daemon verifies artifact hashes and copies them into `models/job_*` atomically. Only then does it write a completed local receipt containing the original local source path, the original local dataset fingerprint, and a local prepared dataset path. Remote absolute paths stay in a separate diagnostic record. This preserves the existing Stage 4-6 provenance checks.

Evaluation and inspection calls for a remote job use the same server's worker and snapshot. An inspection request sends only the selected image and referenced model IDs; the response is JSON plus any preview asset, downloaded into app-owned local output. Benchmark measurements report the server/device used. Export packages are downloaded and verified before the UI offers a local file. Every returned image/file URL resolves through the local daemon.

## Job lifecycle and UI

The header exposes **Compute: This computer / saved server** and a server-management panel. A profile contains a display name, SSH host or alias, SSH port, absolute remote workspace, runtime type and executable/image, and optional GPU selector. A connection test reports SSH reachability, runtime dependencies, visible accelerator, free space, and protocol version; it does not launch a training job. The 3rd stage shows the selected location, snapshot/upload progress, remote device, and job state. Stages 4-6 show the compute location for each result.

Remote job states are `queued`, `preparing`, `transferring`, `running`, `stopping`, `syncing`, `completed`, `aborted`, `failed`, and `disconnected`. Cancel during preparation or transfer stops local staging; cancel after launch sends a job-owned cancellation signal to the worker and waits for a terminal receipt. Closing the app does not cancel a remote job. Reopening the app reads the durable journal and queries that exact remote run. Switching servers affects new jobs only. No automatic fallback to local computation occurs after a remote failure.

## Security and compatibility

SSH targets, ports, runtime identifiers, and remote roots are validated before any process launch. All subprocesses use argument vectors; remote arguments are quoted and restricted to app-owned paths. SSH uses `BatchMode=yes` and `StrictHostKeyChecking=yes`. Profile files contain no credentials. Remote archives reject traversal paths and links escaping the snapshot. Server-side cleanup is limited to the app's own run directory and requires an explicit action. The desktop API token remains in Electron's main process; the renderer never receives SSH secrets or a remote token.

Existing local jobs and APIs retain their behavior. New request fields are optional. A remote server can be Linux/x86_64 with either an existing compatible Python environment or a compatible Docker runtime; CUDA is preferred, but CPU workers are allowed for testing. Any missing dependency or insufficient storage appears in connection preflight before a job begins.

## Verification

Automated tests cover profile validation, SSH command construction, snapshot contents and hashes, symlink handling, transfer errors, cancellation at each stage, network disconnection and recovery, artifact integrity, local provenance, and server switching. A fake worker tests the full local protocol without a GPU. On a configured host, verify SSH/runtime and a harmless worker probe first; a real training run is a separate GPU job and must be started only after its resource use is explicitly authorized. A packaged desktop run verifies the profile panel, target selection, status, and returned local artifacts.
