# Deterministic owned-CPU fixture settlement

The user authorized finishing the remaining CI, UX, guide and integration work.
This subproject repairs two CI fixtures without changing production shutdown,
migration, training, ownership or timeout policy.

## Evidence

PR11 full40189873930 failed its changed-nonce damaged-drain case: a genuine
shutdown guard exited before the ordinary-stop observer published its marker.
The test already permits that exact guard traceback, but requires the marker.
PR12 full40191815419 failed its live-cutover case after join(60), with no worker
error. Local originals both pass; the latter's hosted cause is not established.
Its readiness marker precedes trainer construction and its12-epoch ResNet18
fixture includes large repeated resume writes in the60-second settlement.

## Approach

Controller choice: use the existing fixture-only scheduling techniques. Extend
the original-guard profile barrier to the changed-nonce prepared phase. For live
cutover, use the existing temporary sitecustomize transport to hold the return of
the first genuine training-step status callback until migration is applied.
Then a three-epoch,32px, eight-generated-image actual training fixture can prove
the same worker survives cutover and settles once, with the unchanged60s join.

Changing production guards or increasing deadlines is unsupported. Reducing
epochs without synchronization creates a finish-before-cutover race. Replacing
the trainer/callback or using a -c worker bypass would invalidate the real
execution/ownership proof. The selected transport preserves the genuine -m
backend.training_cli command and original callback execution.

## Required behavior

- The nonce barrier observes only the exact original guard, service thread,
  retained object and intended prepared/draining phase. It neither invokes nor
  catches the guard and retains its exact exception stack and original deadline.
- The training barrier runs only after the first genuine optimizer step and
  successful original status publication for the expected job/spec/output. Its
  unique-token marker is scheduling evidence, not execution authority.
- The original20s readiness deadline crosses into the child unchanged. Neither
  barrier renews deadlines. Original3s,4s and60s assertions remain.
- Same PID/create time, attempt/fence, uncertain reservation, duplicate exclusion,
  old-control bytes, source image hashes, terminal receipt and migration recovery
  assertions remain. Cleanup releases only its own fixture gate before normal
  owned cancellation; no other worker is signaled.
- A60s failure includes bounded status/log/process/observer-stack diagnostics.
  Healthy local completion does not establish the old hosted failure's cause.

## Acceptance

New helper controls fail before implementation and cover wrong phase/code/thread,
foreign job/spec/token, original exceptions, one-shot behavior and deadline
expiry. Exact damaged-drain cases, real live-cutover case and related fixture
regressions pass locally; final exact-head hosted CI remains separate.
