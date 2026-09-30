# P04 model and automated training implementation ledger

Baseline: `a4d3610`. Date: 2026-09-30.

## Scope delivered in this backend increment

- **F031 learned Rotation:** explicit human angle/split truth, frozen source hashes, owned native-pixel preparation, actual CNN direction learning, validation selection, cancellation, offline reconstruction, circular MAE, full-resolution affine correction and transform, project-owned jobs/receipts/models, compatible parent selection, archived evaluation, and a verified TorchScript package/CLI.
- **F037/F038/F042/F112 automated trials:** actual candidate execution across compatible pretrained DINOv3 S/B and YOLO26n/s selections, learning rates, weight decay, input sizes, batch sizes and three applied augmentation profiles. Search budgets bound trial count, requested total epochs and cooperative elapsed time. Each completed candidate records validation metric, actual heldout-input forward latency, full config, checkpoint SHA and completed receipt. Winner selection compares measured completed candidates only.
- **F036/F039/F040/F041 quick/parent reuse:** quick is a single configured real trial; fast retraining loads a compatible completed parent's recorded training configuration and weights, preserves its byte hash and lineage, and starts a new optimizer. Explicit candidate epoch budgets govern the new fit. No exact optimizer continuation is claimed.
- Registry `register_task_runner(task, runner, architectures, metric_key='val_loss', direction='min')` allows specialist runners to supply actual heldout objectives without rewriting the coordinator. A runner receives `TrialContext` with config, paths, device, cancellation, progress and parent; it returns `{metrics, latency_ms, checkpoint_path, epochs_completed, latency_scope?}`.
- Existing DINOv3 classification/patch/segmentation and YOLO detection pretrained defaults remain. Authentic pretrained failures are not replaced by random weights. Existing supervised training now rejects an empty train or validation split instead of publishing zero validation loss.

## API and integration contracts

### Rotation

- `POST /api/rotation/prepare`: `{source_dataset_path, samples:[{image,correction_deg,split,source_sha256?}]}`. Angle is counterclockwise upright correction in `[-180,180]`, with 360-degree circular semantics. Returns `{dataset_path,provenance,sample_count}`. Prepared data belongs to project dataset storage; original images/manifests are not written.
- `GET /api/rotation/manifest?dataset_path=...` reads the owned preparation.
- `POST /api/rotation/train`: `{dataset_path,epochs,batch_size,image_size,width,learning_rate,seed,device,background,warm_start_job_id?}`. Uses standard specialist lifecycle and returns `{job_id,checkpoint_path,model_sha256,result}` or a 202 persisted job.
- Jobs/list/cancel, compatible warm-start parents, models, predict, evaluate and export are available under the same prefix. Evaluation stores `angular_mae_deg`, `within_10_deg`, loss, sample count, checkpoint and prepared-data binding; it does not invent an OK/NG label.
- `predict_rotation_array(checkpoint,rgb_uint8_HWC,device=...)` returns `{correction_deg,aligned_image,transform,source_size,output_size,angle_semantics,checkpoint_sha256}`. `transform` maps **input to aligned output**. Flow must compose the previous source transform with its inverse.
- Model identity: task `rotation`, architecture `small_cnn_angle_v1`, width 8/16/32, image size 16–512, checkpoint `models/rotation/<32hex>/best_model.pt`. Reconstructing the model requires no external weights/download.
- `export_rotation_package` produces TorchScript weights, config, SHA manifest, standalone source/requirements and an image CLI. Model/package/source mutation is rejected.

### Automated training

- `POST /api/automated-training/start`: task, registered source dataset path, preset/device, mode `quick|search|fast_retrain`, budget `{max_trials,max_total_epochs,max_seconds}`, `epochs_per_trial`, search space, base config, objective `val_loss|loss_latency`, `latency_weight`, parent job ID, dataset version ID and background flag.
- Search dimensions: `architectures`, `learning_rates`, `weight_decays`, `image_sizes`, `batch_sizes`, `augmentation_profiles` (`none|photometric|industrial`). These settings change the actual model/training pipeline.
- Capability API declares registered task architectures and objective direction. Jobs/list/cancel persist and read a project-scoped search journal.
- Search response: `{search_id,status,mode,task,budget,trials,winner,configuration_parent,training_provenance,stop_reason,epochs_consumed,duration_seconds}`.
- Completed trial fields: `{trial_id,config,status,metrics,latency_ms,latency_scope,objective,checkpoint_path,checkpoint_sha256}`. Ordinary `job_<timestamp>_<hex>` candidate IDs and completed receipts allow downstream model selection. Parent config has a separate configuration SHA in addition to parent checkpoint SHA.
- Cancellation propagates into the owned child trainer. A cancelled search does not publish a search winner, including cancellation racing final measurement. Time-budget cancellation does not create a completed child checkpoint/receipt.

## Tests and functional evidence

1. Initial RED: six failures for missing Rotation/trials modules and unsupported augmentation profile.
2. Additional RED→GREEN: empty supervised validation; Rotation owned-preparation lifecycle and evaluation support; Rotation parent selector; immutable evaluation dataset binding; final-measurement cancellation race.
3. Final expanded regression command:

   ```text
   python -m pytest backend/tests/test_rotation.py backend/tests/test_automated_trials.py backend/tests/test_p04_training_api.py backend/tests/test_specialized_training_jobs.py backend/tests/test_family_warm_start.py backend/tests/test_warm_start.py backend/tests/test_automl_trainer.py -q
   ```

   Result: **79 passed in 26.65s**. One existing `python_multipart` pending-deprecation warning. Compilation and `git diff --check` passed.

4. Real small CPU Rotation fixture: 20 epochs learn actual angle truth; heldout circular MAE is below 45 degrees; an 83×51 source retains native output geometry; deployed TorchScript and source inference are pixel-identical. This is a generated functional fixture, not industrial quality acceptance.
5. Real pretrained DINOv3 S CPU trial proof: two one-epoch candidates with differing learning rate/augmentation completed in 3.72 seconds. Actual validation losses: `0.7567929029464722` and `0.6396410018205643`; actual heldout-forward latencies: `10.562708290914694` and `7.537000036487977` ms. Winner restored with the download function forced to fail; output was finite `[1,2]`.
6. Verified encoder bytes: 86,362,376; SHA-256 `2a1ec16ae28ffa07bc0ead0241ee7df9fc26451fe6f9f839b7b3afa0a906b040`. The functional receipt is kept outside the repository. Two-structure regression uses a tiny frozen-encoder boundary double while still running the real optimizer/trainer; authentic B/16 and YOLO26s structure-search acceptance were not exercised here.

## Remaining integration and acceptance

- **P04 is not accepted as a whole.** Root owns the native model hub, all-family preparation/selection, main router registration and cross-package acceptance. The flow adapter contract above has been handed off; this increment did not edit `flowchart_engine.py`.
- **Known prepared Patch AutoDL gap:** current trial API binds its `dataset_path` to registered original source, while prepared Patch manifests live in owned project storage. It cannot yet feed that prepared manifest into the trial runner while retaining the canonical original source binding. Existing dedicated Patch prepare/train/evaluate routes are separate. This needs an explicit prepared-input/canonical-source contract, not switching the registered source or writing a manifest into originals.
- Registered primary runner execution covers classification, detection, segmentation and the underlying patch trainer; specialist AutoDL runners are extension points, not yet registered implementations. A full raw-folder CLI/REST bridge and remote specialist execution belong to the later engine/compute work.
- Quantization and device-specific/embedded conversion (F043/F044) are assigned separately. No conversion or optimization flag is counted as implemented here.
- Training wall-clock budgets are cooperative checks; a single blocking framework/download call is not hard-preempted. These budgets are distinct from the runtime hard-deadline work.
- Latency objective is model-forward-only on a real heldout image, not camera-to-PLC or full native-image/DAG latency. All candidates are selected by the declared objective; selection is not operational approval.
- Real industrial angle truth, quality/generalization, authentic larger-backbone structure search, full native UI reopening, multi-OS installation and field equipment acceptance remain separate evidence.
- No commits, staging, push, remote jobs, expensive GPU jobs or live app restart were performed in this increment.
