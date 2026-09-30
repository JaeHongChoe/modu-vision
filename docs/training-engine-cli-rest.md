# Folder and JSON training engine

The engine runs without selecting or changing the desktop project. Each explicit output folder belongs to one task and one original source folder. Use a different output folder for another task/source. Preparation retains native source hashes, creates owned labels/data and binds an immutable input version. Original files are read only.

## CLI

Run from the checkout (or with the installed backend on `PYTHONPATH`):

```bash
python -m backend.training_cli capabilities
python -m backend.training_cli prepare --task rotation --source /data/originals --labels /data/truth.json --output /data/model-output
python -m backend.training_cli train --output /data/model-output --mode quick --epochs 2 --device cpu --config-json '{"width":8,"image_size":64,"batch_size":4}'
python -m backend.training_cli train --output /data/model-output --mode search --epochs 2 --max-trials 2 --max-total-epochs 4 --max-seconds 120 --search-json '{"widths":[8,16],"image_sizes":[64],"learning_rates":[0.001],"batch_sizes":[4]}'
python -m backend.training_cli status --output /data/model-output --run-id <run_id>
python -m backend.training_cli evaluate --output /data/model-output --run-id <run_id>
python -m backend.training_cli predict --output /data/model-output --run-id <run_id> --image inspection.png
python -m backend.training_cli train --config-path /data/model-output/artifacts/<run_id>/configuration.json --mode fast_retrain --parent <winner_trial_id>
```

`train --background` launches an independent local process and returns its run ID/PID. `cancel --output ... --run-id ...` writes a durable cooperative cancellation request. `status` survives CLI/backend reopening; a dead owner becomes `interrupted`. Signals to foreground training request cancellation. Training progress is JSONL on stdout; background progress is saved under `runs/<run_id>/progress.jsonl`. Errors return a nonzero exit code. `--help` describes every command and option.

Genuine DINOv3 and YOLO defaults are preserved. For an offline run, pass `pretrained_checkpoint` and its `pretrained_sha256` in `--config-json`. Missing/incompatible weights fail; the engine does not substitute random weights. CPU, MPS and CUDA selections are explicit and unavailable devices fail. Search exposes only controls applied by the selected family. `capabilities` lists each metric/direction, configuration field and search dimension.

## External labels

Use a UTF-8 JSON object with `samples`. Image names are relative to the canonical source; every sample has an explicit `train`, `val` or `test` partition. Byte-identical images cannot cross partitions. Optional `source_sha256` pins supplied truth to exact source bytes. All three partitions must be nonempty.

```json
{"samples":[
  {"image":"train.png","split":"train","correction_deg":0},
  {"image":"val.png","split":"val","correction_deg":90},
  {"image":"test.png","split":"test","correction_deg":-90}
]}
```

| Task | Sample truth / preparation |
|---|---|
| classification | `label`; flat images become owned class folders. An existing class/partition layout can be used without a labels file. |
| detection / segmentation | `annotations` with native `bbox` `[x1,y1,x2,y2]`, `polygon`, `rotated_bbox` or normal `tag`; alternatively source LabelMe/COCO and an existing split layout. |
| anomaly | `label` normal (`OK`, `good`, `normal`, `pass`) or defect; train must contain normal truth exclusively. Actual normal and defect validation images are required for measured AUROC. Normal truth cannot contain defect regions. |
| patch_classification | Complete region annotations; `--prepare-json` accepts `patch_size`, `stride`, `normal_class`, `minimum_overlap`. The engine records the background-outside-supplied-regions semantics. |
| rotation | `correction_deg` in `[-180,180]`, counterclockwise upright correction. |
| ocr | `text`, actual human truth; `image_size` is height and `image_width` is width. |
| rotated_detection | `label` and `box` `{cx,cy,width,height,angle_deg}`; or `objects` for multiple boxes. Native box bounds and classes are checked. |
| enhancement | Optional `image` selections generate seeded Gaussian noisy/clean pairs; `--prepare-json` accepts `seed`, `noise_sigma`. Explicit native pairs use `image` (before) and `target` (after), with exact split and both byte hashes retained. |
| defect_gan | `bbox` native `[x1,y1,x2,y2]`, at least 16 pixels per side, `label` and split; at least two real train crops. Generator results require human review. |

COCO and LabelMe imports use the established annotation parser. Supply a top-level `splits` mapping from image name to partition, or place images in explicit partition folders. No missing class, region, text or angle truth is fabricated. Enhancement synthesis and GAN generation declare their own semantics.

Preparation revisions have separate label and split scopes. A failed label revision preserves prior preparation. Reusing a recipe preserves selected candidate controls; `fast_retrain` clears former search dimensions, validates compatible parent weights and starts a new optimizer. It is not exact optimizer continuation.

## REST / external UI

The authenticated backend exposes the same engine at `/api/engine` and describes schemas in OpenAPI:

- `GET /capabilities`
- `POST /prepare`: `{task,source_dataset_path,output_dir,labels?,labels_path?,prepare_options?}`
- `POST /train`: `{output_dir,prepared_id?,mode,preset,device,config,search_space?,budget?,epochs_per_trial,parent_job_id?,background}`. `background` defaults to true. A delivered `config_path` can be reused with explicit request fields overriding its recipe.
- `GET /jobs?output_dir=...`, `GET /status?output_dir=...&run_id=...`
- `POST /cancel`: `{output_dir,run_id}`
- `POST /evaluate`: `{output_dir,run_id,device?,split?}`
- `POST /predict`: `{output_dir,run_id,images?:[relative_name],device?,threshold?}`. Omitting images predicts every original image selected by the task's native inventory.
- `GET /artifacts?output_dir=...&run_id=...` returns paths, SHA-256 and byte counts. `GET /artifacts/{model|metadata|configuration|evaluation|predictions}` downloads a verified file, with `X-Content-SHA256`.
- Prediction JSON includes `output_files` with hashes and byte counts. `GET /image-artifacts/{index}?output_dir=...&run_id=...` downloads a verified generated image or GAN review file. Status also verifies these outputs.

Local mode requires the existing process capability token. Shared mode uses account sessions and the selected project's roles: trainers/reviewers/owners can submit; viewers can read authorized results. The source must equal the selected project's registered source, and output must stay under its project storage. CLI is a local process and does not bypass a remote shared server's permissions.

Completed delivery lives under `artifacts/<run_id>/`: `model.pt`, `model_meta.json`, `configuration.json`, plus `artifacts.json`. Evaluation and prediction produce immutable `revisions/<revision_id>/evaluation.json` and `predictions.json`; the artifact manifest points to the latest verified output. Image overlays/aligned/enhanced outputs and GAN review candidates retain original source mapping. Models/metrics/predictions are genuine task outputs; finite validation and measured heldout latency select the winner. Quality approval, deployment, industrial accuracy and full native UI acceptance are separate steps. Training time budgets are cooperative.
