# Public distribution license decision (S6-11)

This record holds the distribution decision for each public artifact scope. The license inventory
(`scripts/license_inventory.py`, S6-01) supplies the evidence. The people with authority make the decisions; this document
does not make them. Until a scope has an approved decision with a named authority that covers every unresolved item in
it, the public release gate holds that scope:

```bash
python scripts/distribution_release_gate.py            # builds the inventory now; exit 0 = releasable, 1 = held
python scripts/distribution_release_gate.py --frozen-build <built backend> --pyz-toc <PYZ-00.toc>
```

The gate's JSON report lists, per scope, the reasons it is held and the exact unresolved inventory ids. Exit codes:
0 releasable; 1 held, including a malformed, stale or edited inventory receipt; 2 the decision record or the arguments
could not be read. A held scope is meant to stop public distribution of that
artifact only. Internal tests, CI and source investigation continue. Nothing runs the gate automatically yet: wiring it
into the release workflow belongs to S6-05/S6-06. LICENSE is not changed in this round.

## How a decision is recorded
Edit the row for the scope in the block at the end:
- `approval_status`: `pending`, `approved` or `blocked`.
- `authority`: who decided, for example the copyright holders or the release owner.
- `decision`: what was decided.
- `accepted_items`: the unresolved items the decision covers, each as `{"id", "license", "version"}` copied from the
  inventory, with one entry per shipped version when an id ships in several versions. A changed license or version
  re-opens the item. `frozen_backend:inventory_required` and `python_environment:requirements_not_satisfied` cannot be
  accepted. They clear only when the gate inventories a built backend (`--frozen-build` with its PYZ table of contents)
  in an environment that satisfies the requirements. The gate does not prove that the build is the locked release build;
  build provenance belongs to S6-05/S6-06.
- `excluded_packages`: packages that must not ship in that artifact.
- `notices`: the file, or list of files, that must list every shipped component of the scope that needs a notice.

The gate re-opens a scope automatically in these cases:
- a new unresolved item appears;
- an excluded package shows up in an artifact (case, `-`/`_`/`.` separators and a category prefix are ignored, and a
  shared library matches with its version in the Linux, macOS or Windows form);
- the inventory receipt is malformed, or differs from a fresh inventory of the same tree, build and commit;
- the scope's notices do not contain a table row with the exact name, version, license and status of a shipped
  component that needs a notice. Rows are read in the generator's columns, outside HTML comments and code fences. The
  gate does not interpret headings, so a matching row under a heading such as "not shipped" still counts. Trained-model
  rows are matched on name, terms and status.

The `source` row's `license_file_sha256` must match LICENSE. A license change therefore holds both the source and the
installer until a decision records it. After a decision, THIRD_PARTY_NOTICES.md and docs/model-license-matrix.md are
regenerated with the S6-01 tool, so the notices match what ships.

## Scopes and the questions to decide

### source: public repository
Covers first-party code under MIT and the project assets (logo, icons). The asset license or trademark terms are not
stated yet.
- Decide: the license of the first-party code (keep MIT, or change it if the review finds that necessary and the
  required consent exists), and a license or trademark policy for the assets.

### installer: frozen backend, renderer bundle, Electron runtime
- The frozen backend currently bundles the detector package `ultralytics` and `ultralytics-thop`, which are AGPL-3.0,
  next to MIT first-party code. Options:
  - (a) ship detection as a separately installed optional pack, so the installer itself does not include the package
    (whether that is sufficient is part of the review);
  - (b) distribute the combined installer and meet the obligations the review finds the AGPL-3.0 places on it;
  - (c) obtain a commercial license for the shipped scope;
  - (d) use a permissively licensed detector implementation (a recipe change outside S6-11).
- Whether first-party code can be relicensed, and whose consent that needs, is part of the owner's review.
- As the S6-01 inventory records, separating code into modules or plugins does not by itself remove license
  obligations; the decision has to cover each distributed artifact as a whole.
- The Electron runtime bundles Chromium, Node.js and FFmpeg under their own licenses. Their notices come from the exact
  shipped build, including the FFmpeg component flavour.
- Python dependencies must be inventoried from the locked release build (`--frozen-build`), not from a development
  interpreter.

### optional_pack: DICOM, OpenVINO and transformer packs
Covers optional packs installed by the user.
- Decide: inventory them in a pack build environment and record their notices before any pack is published.

### remote_worker_image: Dockerfile built by the server owner
The published Dockerfile installs the AGPL-3.0 detector package and caches model weights.
- Decide: whether it is enabled by default; what the image notice says; and whether the project ever publishes a built
  image.

### runtime_download: weights fetched from their providers at run time
Covers DINOv3 backbones (the provider's own license, not OSI approved), detector weights (AGPL-3.0) and torchvision
weights (weight and training-data terms not reviewed).
- Decide: whether the app may download them for the user, or only after the user accepts the shown license terms; and
  confirm that no public artifact bundles them.

### trained_export: models users train and export
Exported packages built on base weights may carry the base weights' terms. The inventory records DINOv3 and detector
derivative terms as needing a decision; the terms for torchvision weights are not established.
- Decide: the notice and acceptance text inside an export package, and whether export is refused without it.

<!-- distribution-decisions -->
```json
[
  {"artifact_scope": "source", "license": "MIT (first-party code); asset terms not stated", "notices": "LICENSE",
   "authority": "", "approval_status": "pending", "decision": null, "accepted_items": [], "excluded_packages": [],
   "license_file_sha256": "654918d8bec2a70f2023728c32a631f2bb901834354878eea40ee380f3d10864"},
  {"artifact_scope": "installer", "license": "MIT first-party with bundled third-party components", "notices": "THIRD_PARTY_NOTICES.md",
   "authority": "", "approval_status": "pending", "decision": null, "accepted_items": [], "excluded_packages": []},
  {"artifact_scope": "optional_pack", "license": "per pack, not inventoried yet", "notices": "THIRD_PARTY_NOTICES.md",
   "authority": "", "approval_status": "pending", "decision": null, "accepted_items": [], "excluded_packages": []},
  {"artifact_scope": "remote_worker_image", "license": "built by the server owner from the published Dockerfile", "notices": ["THIRD_PARTY_NOTICES.md", "docs/model-license-matrix.md"],
   "authority": "", "approval_status": "pending", "decision": null, "accepted_items": [], "excluded_packages": []},
  {"artifact_scope": "runtime_download", "license": "each provider's weight terms", "notices": "docs/model-license-matrix.md",
   "authority": "", "approval_status": "pending", "decision": null, "accepted_items": [], "excluded_packages": []},
  {"artifact_scope": "trained_export", "license": "derivative terms of the base weights", "notices": "docs/model-license-matrix.md",
   "authority": "", "approval_status": "pending", "decision": null, "accepted_items": [], "excluded_packages": []}
]
```
