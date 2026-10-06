# Task135: authentic prompt and full remote resume controls

## Software outcomes

S3-06 and S4-12 now have actual learned-model and native control evidence.
This closes their software implementation dimension. Human annotation,
semantic/model quality, physical devices and final acceptance remain unapproved.

| Scenario | Observed result |
|---|---|
| Authentic local SAM2.1, Grounding DINO and DINOv3 | Official immutable weight revisions and upstream hashes; actual masks, grounding and image-example features. No runtime download or external image transfer. |
| Native long bilingual text and positive/negative images | Exact text/examples transmitted to the local provider; actual pending candidates, rejected review, reopen and async batch cancellation. Korean semantic quality is not approved by this synthetic control. |
| Native few-label update | Two-epoch authentic DINO feature classifier, separate refined child model, preserved parent checkpoint and all source images/labels. |
| Local AutoDL | Actual finite trial metrics/latency, reopen and immutable completed-trial reuse with zero new epochs, actual one-epoch retrain and observed cancellation. |
| Local/remote trial parity | Same immutable dataset version, content, configuration, seed, objective and budget; actual CPU and owned CUDA executions and completed-trial reuse. |
| Remote full checkpoint resume | Actual partial job, resumed job and uninterrupted eight-epoch job. All model, optimizer, scheduler, scaler, RNG, early-stopping, next-epoch and global-step states match exactly. |
| Native resume choice | Genuine owned terminal worker state, original profile probe and selected state; start enabled without requiring unrelated fresh pretrained weights. |

## Bugs found through actual execution

- A remote start journal omitted project/account ownership and budget metadata;
  resumed-job selection correctly refused it. The direct launch now carries
  those fields and the caller's queue policy.
- Reusing a completed AutoDL search without an explicit dataset version created
  a new version. It now reuses the original immutable version after validating
  current source/labels/task/policy; a changed source is refused.
- Small feature fits can complete before their start response. The native
  panel now loads the returned completed model immediately.
- Polling previously marked a job terminal before fetching its model list,
  cancelling its own effect while that fetch was in flight. The model fetch
  now finishes before the running state changes. The failed actual trace was
  retained and the full native scenario then passed.
- Corrupt training state cannot fall back to unsafe loading, and a corrupted
  cancellation checkpoint creates a terminal failure record rather than an
  uncertain abandoned status. Remote envelopes are bounded, immutable and
  checksum/recipe/content bound before transfer or restoration.

## Verification receipts

Private Task135 receipts preserve exact source/input hashes, commands, actual
job journals, stdout, model states, GUI screenshots/traces and before/after
GPU and shared lease inventories. Model weights and user records are excluded
from publication.

- Full remote summary SHA-256:
  `4d03c3a0b625bbf352c41806908870b303f7d9275eb9f3c69a26c22f97d22248`.
- `remote-resume-final-safety-third.xml`: 140 passed.
- `closure-renderer-current.log`: 827 passed, zero skipped.
- Current renderer/main/E2E TypeScript checks: passed.
- `foundation-native-refine-third.log`: actual native learned inference,
  fit/refine/review/reopen/cancel passed in 2.3 minutes.
- `autodl-native-retrain.log`: actual native measure/reuse/retrain/stop passed.
- `remote-resume-native-owned-third.log`: actual original-profile resume
  choice and start-readiness control passed.

The Linux hosted gate, frozen application known-image execution, 72-hour soak,
Windows native qualification, publisher signing and final release decision
are separate receipts. They are not inferred from this document.
