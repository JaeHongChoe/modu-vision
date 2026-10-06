# Actual cohort, historical context and frozen package controls

## Learned comparison and whole-flow execution

Five authentic DINOv3 small classifiers train for two epochs each, with distinct
seeds/checkpoint hashes and one immutable dataset version. Training, validation
and the three-image heldout cohort have disjoint generated image bytes. The
actual Electron comparison controls select model A/B, persist/reopen the result
and export the evidence JSON.

The saved single-model and five-distinct-checkpoint graphs evaluate the same
frozen heldout cohort. Explicit **synthetic control truth** covers one normal and
two defect cases. All five model nodes execute without error/warning/skip. A
separate exported package process executes each input; decisions, nodes, ROI
and spatial evidence match the reference flow. Original/checkpoint bytes remain
unchanged. All three predictions are NG; this is execution evidence, not a
quality result or human approval.

- Native source: `9ae77cd` plus the two retained new cohort fixture/spec files.
- Native harness manifest SHA-256:
  `049eddb66c2f8a512cd2b5aff27fb129ef6ff38197a1578f4d93546b0a5fa857`.
- `trained-cohort-third.log`:1 actual native test passed in1.2 minutes.
- `comparison-package-current-second.xml`:92 contract cases passed.
- First two harness failures and the disk-full contract run remain retained.

These controls close S4-13 software implementation. Representative process
quality, independent final review, deployment and target acceptance remain
unapproved. The five-checkpoint recipe also contributes to S7-02; the other
model-family/human-data scenarios remain required.

## Explicit ended-history converter

`backend.engine.historical_job_binding` is an offline operator tool for already
owned installations, before generation cutover. It binds only exact existing
legacy-import rows in completed/failed/aborted local states to their original
registered project and installation local actor. The historical actor and
source digest remain in an audited same-state event and durable receipt.

```sh
python -m backend.engine.historical_job_binding preview --root OWNED_ROOT
python -m backend.engine.historical_job_binding apply --root OWNED_ROOT \
  --expected-preview-sha256 REVIEWED_SHA256 --reason "Reviewed ended local history"
python -m backend.engine.historical_job_binding receipt --root OWNED_ROOT \
  --binding-id RETURNED_BINDING_ID
```

The converter validates schemas/source hashes before opening any upgrading
store constructor. Source/model bytes are preserved. CAS refuses changed
sources/control rows; repeat apply reads its durable receipt without overwriting
later writes, including after forward generation cutover. A reproduced parent
traversal escape is now rejected against the resolved owned root.

Foreign/copied sources, links, invalid identities, mismatched project manifests,
team authority, interrupted/live workers, active attempts and uncertain leases
refuse. Runtime recovery indexes require a separate reviewed ownership adapter.
No worker launch/adoption/lease authority is created. No existing user installation
was migrated. S1-08 remains incomplete for live-worker and historical adapters.

`migration-publication-reviewed.xml`:72 passed. Initial traversal failure and
the invalid test fixture's foreign-key failure remain retained.

## Process and frozen application evidence

Current process/package/source-gate controls pass171 with two native-Windows-only
skips. Actual spawned DataLoader children stop only with their owned parent;
an unrelated process and a reused PID remain alive. The frozen launcher dispatches
real allowlisted worker modules and packaged CPU operations run without an
external Python/Node path. This closes S1-09 software implementation; restricted
Windows11 installation/ACL/device acceptance is not inferred.

[Windows packaged run37438929535](https://github.com/JaeHongChoe/modu-vision/actions/runs/37438929535)
on exact `9ae77cd3f082cfd3cc2fe1183dae4c71f9f518bf` passes unsigned NSIS/portable
build, actual packaged Electron/frozen CPU train/evaluate/infer/export and two
launches with identical persisted restart evidence. Downloaded executable sizes
and SHA-256 match the runner inventory. All1533 source checkout hashes match:
18 exact bytes,1515 explicit LF-to-CRLF checkout equivalents. Owned process
observers report no remaining children on either exit.

The public sanitized receipt is `docs/verification/receipts/windows-packaged-9ae77cd.json`.
It preserves exact executable/receipt hashes and the unqualified conditions.
Windows11 installation, uninstall, SCM/Session0, hardware, update recovery and
signing remain unverified. Windows actual QA is user-waived, not passed. This
package freeze does not include the subsequent historical converter.

Actual macOS frozen backend known-image and packaged application CPU controls
also pass twice on their retained freeze. Known-image manifest SHA-256 is
`a2172268f53aca6c68357c8943e94f92388fde371ad1b787d5ca8f1548449fa8`;
backend executable SHA-256 is
`553dc37398c9481335d1154b8d7c95b22e14af9f8a1b22639abb3615eaa7d913`.
This is unsigned development runtime evidence, not signed installation/update
or representative model-quality acceptance.

## Hosted regression correction

The prior exact9ae Linux run retained1933 passing CPU cases, six skips and one
publication-barrier timeout. Its reader did not observe a premature completion;
the fixture could not reach the receipt barrier within five seconds. The fixture
now isolates unrelated GC/cache cleanup and confirms its competing reader starts
before testing the barrier. Production completion ordering and the receipt/hash
checks are unchanged. Six focused publication cases and the combined72-case
suite pass. New exact-source hosted CI must complete independently.

The Linux CPU gate now includes whole-flow/package precision parity and owned
global/historical migration controls. Public runs remain read-only and receive
no GPU/signing/deployment credentials. Authentic-weight native controls remain
explicit opt-in outside public CI. No final acceptance or release promotion is
created by these controls.
