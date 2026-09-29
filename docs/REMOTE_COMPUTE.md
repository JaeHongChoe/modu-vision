# Remote compute setup

Remote compute uses the user's existing OpenSSH key or agent. The desktop app keeps datasets, annotations, saved splits, model history, and the API token on the local computer. It transfers one prepared snapshot and a versioned worker bundle to an isolated run directory, then verifies returned file hashes before registering a completed model.

## SSH and workspace

1. Confirm key-based access and the host key from an administrator-approved channel. OpenSSH must already trust the host in `known_hosts`; the app uses `BatchMode=yes` and `StrictHostKeyChecking=yes` and will not accept an unknown key automatically.
2. Choose a dedicated workspace owned by the SSH account, such as `/data/home/kai/modu-vision`, with permissions `0700`. Keep this separate from existing server projects, containers, and services. The worker uses only `runs/<job_id>` below this root. Provisioning the directory is an operator action. The transport enforces `0700` on `runs/` and each run directory and `0600` on uploaded files; it does not change permissions on the workspace root.
3. Select a compatible Python executable or an isolated Docker worker image. The app does not save a password, private key, or SSH token in its profile file.

The app user data directory contains `compute_profiles.json` and the remote job journal. Electron sets `VISION_AI_STUDIO_USER_DATA_DIR` to its user data directory; standalone backend runs default to `~/.modu_vision`.

## Runtime choices

### Python

Set `runtime_kind` to `python` and `runtime_value` to one Python executable, for example `/data/home/kai/venvs/modu-vision/bin/python`. Install the worker imports into that isolated environment: torch, torchvision, OpenCV, NumPy, Pillow, scikit-learn, psutil, FastAPI, and Pydantic. ONNX and ONNX Runtime are needed for ONNX export and its inference smoke test. Avoid changing an existing project's environment. The worker bundle is uploaded to each run's `code/` directory and added to `PYTHONPATH` for that run. An empty GPU selector sets `CUDA_VISIBLE_DEVICES` to an empty value, so Python runs on CPU; a specified selector exposes only that GPU.

Training initializes several torchvision models with default pretrained weights. Before using a Python profile, run [`cache_weights.py`](../build/remote/cache_weights.py) with that isolated Python interpreter to cache all supported default checkpoints and write `~/.cache/torch/weights-manifest.json` (or a manifest under a dedicated `TORCH_HOME`). The probe hashes each cached file and compares it with that manifest. Keep `TORCH_HOME` and `MODU_VISION_WEIGHTS_MANIFEST` consistent between provisioning and the SSH runtime environment. A missing or mismatched checkpoint makes preflight unready before a job can start.

### Docker

[`build/remote/Dockerfile`](../build/remote/Dockerfile) defaults to the PyTorch 2.6.0 CUDA 12.6 base image digest observed in Server 42's local Docker cache on 2026-09-29. It installs matching torchvision 0.21.0 and the training, evaluation, and export imports without copying worker source into the image. The [PyTorch version matrix](https://docs.pytorch.org/get-started/previous-versions/) lists this torch/torchvision/CUDA pairing. The build downloads all seven supported default pretrained checkpoints, verifies their torchvision URL hash prefixes, and records full SHA-256 hashes in `/opt/modu-vision/torch-cache/weights-manifest.json`. `TORCH_HOME` points to this readable image cache for the non-root runtime UID. Building needs network access to the official weight URLs; worker containers run with `--network none` afterward.

After the server owner approves resource use and an isolated destination, stage both `Dockerfile` and `cache_weights.py` together on the server and build a separately named image, for example from that staging directory:

```bash
docker build --pull=false -f Dockerfile -t modu-vision-worker:20260929 .
```

The Docker profile sets `runtime_kind` to `docker` and `runtime_value` to that image tag. The transport mounts the profile's remote root at the same absolute path inside the container, sets the run's `code/` directory as `PYTHONPATH`, and launches the worker as the SSH account's numeric UID:GID so artifacts remain readable and cancellable over SSH. It sets `HOME` and `XDG_CACHE_HOME` inside that private run directory because the SSH UID may have no home directory entry in a generic image. It does not expose a port or use an HTTP service. When GPU access is requested, Docker uses the profile's GPU selector; `all` exposes all visible devices. Pick a device only after checking server allocation.

## Example Server 42 profile

This is an example of the fields to enter in the app after the isolated workspace and runtime exist. It is not applied by this document.

```json
{
  "name": "Server 42",
  "ssh_target": "operator@gpu-server.example.com",
  "ssh_port": 22,
  "remote_root": "/data/home/kai/modu-vision",
  "runtime_kind": "docker",
  "runtime_value": "modu-vision-worker:20260929",
  "gpu_selector": null
}
```

The app generates the profile `id`. Leave `gpu_selector` empty for CPU-only probe and runs in either runtime; choose a GPU selector for the intended run after resource allocation. Server 42 is a profile example, not a built-in destination. The local computer remains the default selection.

## Connection test and job behavior

The **Test connection** action checks SSH, runtime imports, all seven pretrained checkpoints and their SHA-256 manifest, workspace existence, free space, protocol version, and visible device. The Python probe uses a read-only `python -B -c` command. The Docker probe uses a read-only bind mount and container filesystem, runs as the SSH account's numeric UID:GID, and mounts an ephemeral 64 MiB `/tmp` for library imports. It starts no training job and writes no run data. A probe can report reachable SSH and still report `ready: false` when the isolated runtime is incomplete.

Training prepares a local task-ready snapshot, including LabelMe Studio overrides and the saved split, before upload. Relative paths and SHA-256 digests identify the transferred files. After launch, a run writes durable `status.json` and `artifacts.json`. A network interruption leaves the result unknown until reconnect; it does not start a local replacement. Cancellation during local preparation stops local work. Cancellation after remote launch sends a `cancel` file into that run and waits for a terminal result. Closing the app leaves remote work running.

Downloaded checkpoints and other assets must pass the manifest hash check before the local app records completion. Evaluation, inspection, benchmark, and export for a remote job stay bound to that job's server. The UI serves returned previews and packages through local paths.

Stage 5 saves a separate flow for each dataset folder and recipe. Classification, anomaly, and segmentation can inspect a full image with their single trained model. Detection has a detector-only flow: boxes above the selected confidence threshold count as defects, so one or more boxes yield NG and no boxes yield OK. The optional detector ROI flow requires a detection model and a second inspection model. A changed local snapshot is rejected before a remote evaluation or inspection result is associated with its source image.

Cleanup should name a specific completed or abandoned `runs/<job_id>` directory and be approved by the server owner. Do not remove the shared root or unrelated Docker resources.

## Verification boundary

On 2026-09-29, the isolated workspace `/data/home/kai/modu-vision` and staging directory `build-20260929` were created with mode `0700`. The staged Dockerfile and cache script have mode `0600` and SHA-256 hashes `80bd1979c1c4d7034fbb9c56ae8702a5c9d04b224df1c19d3b47e8508c124cd3` and `063207683e7ac3efac2c0307c48bba74b0ca6aba214bbdfc632ca24953733eea`, respectively. An isolated `docker build --pull=false` produced `modu-vision-worker:20260929` with image ID `sha256:b0a8d62a8a9c0b5c77549cdef4418ebbe81f392aee883d9b5175457189c1a418`.

Live, read-only probes under the SSH account's numeric UID:GID returned `ready: true` for CPU-only (`gpu_selector: null`) and for GPU selector `2` (`NVIDIA L40S`). All nine required imports and seven cached checkpoint hashes passed in both probes. At the occupancy check, GPU 0 and 1 had other processes, while GPU 2 and 3 showed 0 MiB; this is only a momentary observation, not a resource reservation. Check allocation and repeat the probe before running a GPU job.

The desktop user data now contains a selected profile `server42-l40s-2` for GPU 2. A fresh profile probe returned `ready: true`, `device_type: cuda`, and `NVIDIA L40S`. Saving and selecting this profile did not start a GPU job.

For functional QA, the 80 paired LabelMe examples in `/Users/kai/Documents/test_imgage` were prepared with the saved split. A portable archive of 160 image and mask files (3,255,889 bytes) excluded the local-only `source_manifest.json`; its local and remote SHA-256 both equal `311cdd615b885fa9649eb22b974c82308ada94707e429dc4a853bb500c55807e`. A separate eight-pair subset was used for a one-epoch **CPU** smoke run on Server 42 with a QA-only 2 CPU/8 GiB Docker cap. Job `job_1790680649_remoteqa` completed with a verified 31,460,034-byte local checkpoint and receipt. The same job completed remote evaluation (two test predictions), single-model flowchart execution (four steps, NG verdict, preview), single-image inference (PNG overlay), benchmark, TorchScript export, and ONNX export. These checks validate the feature path, not model quality. A second capped CPU job, `job_1790681070_cancelqa`, was cancelled after the worker reported `running` and step 1; both remote status and local receipt ended `aborted`, with no checkpoint. No GPU training was run.
