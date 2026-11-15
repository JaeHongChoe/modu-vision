# First-use pilot and feedback

Status: protocol ready; human pilot execution pending. Agent checks, synthetic runs and package smoke are not human acceptance. All ten [model families](pilot-model-matrix.md) remain in scope.

## Participants and task coverage

Recruit 3–5 first-time product users spanning inspection operator, label/quality reviewer and model/project owner roles. Record domain/AI experience and accessibility needs with anonymous participant IDs. With three people use P1–P3; with four/five redistribute specialist tasks to P4/P5 while retaining all ten families. Moderator demonstrations never count as participant completion.

| Participant | Primary end-to-end task | Additional family tasks |
| --- | --- | --- |
| P1 | Classification using approved own images | Patch classification, OCR |
| P2 | Detection starting with unlabeled images | Rotated detection, rotation |
| P3 | Segmentation with mask/polygon truth | Anomaly, enhancement, defect GAN |
| P4 optional | OCR and rotation end-to-end | Repeat an observed blocker |
| P5 optional | Enhancement or GAN train/evaluate/generate/review | Repeat anomaly or patch path |

Each assigned family gets purpose/prerequisite/output explanation and prepare/start-or-correctly-refuse tasks. Each primary task gets the lifecycle below. Specialist tasks must reach actual evaluation and prediction/flow or GAN review when fixture/runtime permits; otherwise record limited walkthrough or blocked/pending, never completed execution. Add follow-up sessions when needed. A single participant per family is exploratory coverage, not a population estimate.

## Preparation and moderation

Freeze source commit and candidate artifact hash. Distinguish source browser, isolated candidate app and installed app; record macOS/browser/device and capability response. Verify fixtures beforehand, separately from the human record. Use small authorized copies with real family truth, immutable splits and a frozen heldout set; label synthetic demos explicitly. Agree on recording consent, retention, access and deletion. Preserve production data/services.

Plan 60–90 minutes per session; move extra families into follow-ups. Allow 10 minutes for the built-in demo, explaining its synthetic scope. Read the goal without button instructions. Ask participants to think aloud. Set timeboxes before observation: suggested 5 active minutes for navigation/preparation, 10 for labels and 5 to submit/inspect each execution; log machine waiting separately. Stop for privacy/data-loss risk. After timeout or a help request, prompt neutrally and record exact assistance. Procedural help makes the result assisted.

## Stage protocol

Copy the [session template](pilot-session-template.md) per participant and repeat rows for each family. Every stage records start/end, active/waiting seconds, criterion, help, result, blocker and recovery. Help: H0 none; H1 goal reread; H2 conceptual hint; H3 procedural instruction/moderator action. Separate unassisted success, assisted completion, blocked, timeout, skipped and abandoned.

| Stage | Observable success | Challenge and recovery observation |
| --- | --- | --- |
| Choose/create | Choose intended family, explain output/truth, create project at intended location | Open/close help by keyboard with focus return; selection must not execute |
| Import/inspect | Identify source photos, labels, classes and split state | Unlabeled detection/segmentation directs to labeling, not re-import or false readiness |
| Label/prepare | Save actual family truth and independent train/val/test | Correct missing label/string/angle/pair; incomplete context remains blocked |
| Train | Identify dependencies/device, start explicitly once, locate job and terminal artifact | Safe missing-input/device fixture: explain cause and recover without duplicate job |
| Evaluate | Use frozen heldout set, inspect an error and explain metric limits | Missing/stale evaluation cannot imply approval; do not change test truth to improve score |
| Flow/save/reopen | Non-GAN: edit/save/reopen intended model/thresholds, run heldout image and inspect version/result | Recover from stale model/input guidance without losing saved work |
| Generate/review/adopt | GAN only: generate, inspect/reject or explicitly approve train candidate | Explain generation is not inspection and cannot populate val/test; unsupported adoption stays pending |
| Error/recovery/finish | Find actionable error, inspect saved result before retry and state next action/approval boundary | Record failed recovery, abandonment and mistaken OK; export/target execution require separate evidence |

Use reversible invalid-input fixtures, never corrupt participant work or deliberately interrupt live services. Preserve the original failure before helping participants reach later stages. Ask what output they trust, what remains unapproved and their next action. Keep observation/quotes separate from interpretation and proposed fixes.

## Release blockers and follow-up

Predeclare decision owner, intended scope and any completion/time thresholds before results. These block public pilot-candidate release until fixed and re-executed on the affected path: data loss, unauthorized access, incorrect OK/quality approval, stale approval/version shown as current, silent device/model substitution, GAN presented as inspection or generated data admitted as heldout truth.

An unrecoverable primary-task blocker or unexplained missing preparation/execution path blocks a broad novice-ready claim. Missing family execution, artifact smoke or human records remain explicit gaps; limit release claims to verified scope without removing families to hide gaps. Report counts/denominators per family, stage and help level. Three to five users do not establish industrial accuracy.

For every blocker retain redacted reproduction, severity/owner, tracked requirement/issue, fix commit and same-path rerun. Link session records and fixes into a dated release decision; required missing evidence prevents approval. Redact real images, secrets and private paths in shared issues. Later sessions validate their tested versions only. Customer truth, target hardware, signing and production acceptance need separate evidence.
