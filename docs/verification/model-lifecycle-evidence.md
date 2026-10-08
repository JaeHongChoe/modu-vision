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

Prepared family input inventories, GAN adoption, legacy records without immutable
split binding, and installed target qualification remain pending in this adapter.
Consistent records can still be fabricated: hashes establish byte integrity and
record consistency, not the authenticity of the original execution. Runtime
reproduction, representative human truth, model quality, signing, target execution,
and final acceptance require their own evidence.
