# Model lifecycle record integrity

`scripts/check_model_lifecycle.py` checks the declared durable records for one
model. It reads files, compares their full raw hashes, and joins their identities.
It does not load a checkpoint, execute a model, or write files.

## Run

Copy original records byte for byte into one absolute artifact directory. Keep
the original project, source, version, job, and image identities inside those
records. Paths in the wrapper are relative to that directory. Linked files and
linked directory ancestors are refused.

```sh
python -B scripts/check_model_lifecycle.py \
  --root /absolute/artifact-directory \
  --receipt lifecycle.json \
  --receipt-sha256 <raw-wrapper-sha256> \
  --receipt-size <exact-wrapper-byte-count>
```

The wrapper has `schema_version`, `family`, `dataset`, `labels`, `target`,
`train`, `eval`, `flow_or_adoption`, `export`, and `hashes` fields. Its `hashes`
map pins every referenced regular file by exact byte count and raw SHA-256.
Producer semantic digests are checked separately. The tests in
`backend/tests/test_model_lifecycle_evidence.py` demonstrate the supported
wrapper and the refusal cases.

Exit 0 means all declared stages have consistent record integrity. Exit 1 means
a required stage is pending or refused. The JSON output identifies those stages.
Missing stages must remain missing; do not create replacement evidence.

## Scope

Supported joins cover a dataset version and its members, a source and labelset
receipt, completed training and checkpoint metadata, evaluation history, a saved
flow, and a package with `flow_parity_v1` records. Split digests, graph identities,
package members, model references, input hashes, and recorded outcomes must agree.

The optional `train.family_files` map preserves original absolute producer paths
and maps each to an exact relative byte copy. Prepared OCR manifests and prepared
Patch classifier manifests/source maps are supported when original UUIDs, input
and label hashes, immutable version/split membership and approval eligibility
agree. OCR requires both original job and checkpoint dataset paths. Generic
Patch metadata may omit `dataset_path`; its exact `training_provenance` still
must equal the original job binding. A present metadata path must match.

Missing family copies, unsupported formats, GAN adoption, legacy records without
immutable split binding, and installed target qualification remain pending.
Consistent records can still be fabricated: hashes establish byte integrity and
record consistency, not the authenticity of the original execution. Runtime
reproduction, representative human truth, model quality, signing, target execution,
and final acceptance require their own evidence.

## Retained local controls

The prepared-input correction passed 42 isolated regression controls, retaining
the original 26 cases. Original OCR record copies verified all seven declared
stages. Original classifier and Patch classifier copies verified dataset, labels
and training only: their missing qualifying evaluation leaves later stages
pending. These offline checks reproduce record reading, not training or runtime.

Separate native app executions exercised rotation-to-OCR and same-source
SEG/Patch CPU flows and package comparisons. Their synthetic controls are
documented separately; they do not approve representative manufacturing quality.

## Evaluation producer context correction

A causal test reproduced the original missing `training_provenance` key. The
correction passed 41 regression controls plus 19 existing source-handoff, label
history and bounded CPU recipe/archive controls. It retains a detached full
original binding only when version, split, fingerprint, labels and optional team
data agree, including the actual manifest `size_bytes` contract. Malformed or
foreign metadata remains absent; no original record is rewritten.

This context helper is a producer-coherence check. Strict downstream raw artifact
readers still establish byte and path integrity. The existing original Patch and
classifier lifecycle readbacks remain partial. New control hashes and exact
execution scopes are in `2026-10-09-evaluation-provenance-controls.json`.
