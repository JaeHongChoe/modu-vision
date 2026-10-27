# 2026-10-28 integration regression repairs

Product repair source through `332e01e0a0a0bfac5c709af1dc30a9514b06b664`. This document does not qualify an unpublished child or replace exact-head hosted full regression.

## Retained hosted failures

- PR19 exact `d54a59c3`, [full40529045198](https://github.com/JaeHongChoe/modu-vision/actions/runs/40529045198): source5789passed/20skipped/2failed, browser350passed/2skipped/1failed. Batch input-change expected INPUT_CHANGED but received EXECUTION_ERROR; its underlying error was absent. Daemon second-start identity read observed empty argv; comparison POST202 completed after the original10-second frame.
- PR20 exact `23ee7fce`, [full40529054013](https://github.com/JaeHongChoe/modu-vision/actions/runs/40529054013): browserSUCCESS; source5790passed/20skipped/1failed on the first-start daemon identity read.
- PR21 exact `753da965`, [full40529063313](https://github.com/JaeHongChoe/modu-vision/actions/runs/40529063313): sourceSUCCESS; browser353passed/2skipped/1failed on the final F064 project return.
- PR18 exact `4549515f`, [full40556106272](https://github.com/JaeHongChoe/modu-vision/actions/runs/40556106272): browser349passed/2skipped/1failed. The source-empty control test still expected a null Task Center source envelope after that response had been normalized to empty string. It stopped at this baseline expectation before the later reload checks. The source job was still running when this browser result was recorded.
- No full rerun, cancellation, gate retry, merge or branch deletion was used to erase these results. PR18 ca52 full/gate success remains historical qualification of ca52 only.

## Shared project readiness

The failed F064 trace showed the original flow loading/saving refusal before the target selection POST. The enabled shared selector omitted the canonical project-switch readiness guard. The component now subscribes to that existing guard; the handler/authority checks and local-authentication-only exception are unchanged.

RED2 expected component failures became24/24GREEN; related component checks89/89, renderer/main2505PASS/one explicitSKIP, types and harness67/67 pass. The original F064 browser case passed once locally unchanged, including A/B/A, cache results and protected-state/owned-cleanup checks. That local pass does not replace hosted qualification.

## Comparison admission inventory

One instrumented original CPU browser attempt passed with the original10-second response/cancel assertions. Its local ACK was dominated by input binding (98.51%); view and JSON serialization were not dominant. One separate pure-binding probe returned an identical512-image binding and left1576 fixture files unchanged. These are diagnostic observations, not clean-source or CI qualification, and did not reproduce the CI9.8-second duration.

The500+12 pages rebuilt the complete metadata inventory and dataset summary twice. Full comparisons now use one internal read of the same authorized inventory. HTTP gallery pagination still rejects limits above500. Bounded selection retains its prior limit, incomplete full inventory still fails409, and every item path/shared-symlink/decode/SHA/label check remains unchanged. No cache, worker/view shortcut, timeout change or truth/publication relaxation was added.

A real512-tiny-PNG regression observed2 inventory builds before the repair and1 afterward. Eight controls pass: exact sorted images/digests/labels, partial limits, fresh subsequent reads, corrupt image after500, incomplete count, and HTTP500/501. Two existing non-inference truth controls pass. Independent review approved the exact diff.

## Empty project Task Center response

An existing authenticated localhost UI GET returned200 with source_dataset_path=null, default labelset and empty task/error/reservation arrays. The renderer correctly rejected that envelope against its canonical empty-string source. The server response envelope now maps only None to empty string. The stored project, internal source filters, labelset/authority checks and strict client comparison are unchanged.

The fresh-project API test failed on None versus empty string before repair; afterward9 recovery/API cases plus1 existing source/labelset filter pass. Component refresh10 and related task controls12 pass, retaining foreign-source/labelset/null-envelope refusal. All three project manifests remain byte-identical. Actual updated-server UI readback remains pending activation.

The retained PR18 browser failure exposed one stale E2E baseline expectation. Only the Task Center response-field assertion is updated to exact empty string; the persisted project source still must be null. The original browser case then passes1/1 locally through reload, unchanged readiness/grouped-split refusal,422 responses, exact project/original-tree preservation,10-second read frames and zero retries. E2E types pass. This local correction does not qualify a new hosted head.

## Unresolved runtime evidence and boundaries

The old batch/daemon source causes remain unresolved; production runtime ownership code is unchanged. Failure-only test notes now expose bounded classifications/structured outcome fields and non-authoritative process observations. They never accept empty argv, retry ownership, add signals or add OS reads on success. Twenty-two controlled diagnostic checks and11 existing production diagnostic checks pass; privacy review closed raw error-string leakage. The200ms observation frame limits the start of new OS reads, not completion time of an already-started synchronous call.

New PR18 `c054fc44` [fast40550483420](https://github.com/JaeHongChoe/modu-vision/actions/runs/40550483420) passed11/11 and security succeeded before the later inventory/envelope repairs. Its Linux daemon file11/11 and batch file23/23 passed. This does not fix or reclassify the older full failures, nor qualify the later repair source.

No real provider request, user-image/model/training/evaluation run, credential-file read, operating-system security approval or deployment was performed by these repairs. One separately approved real GPT read-only diagnostic remains unsent. Owning-app AX/screenshot reads time out, direct SecurityAgent selection is explicitly denied, and the actual presence/content of a security prompt is unconfirmed.

## Current production file identities

| File | SHA256 |
| --- | --- |
| `src/renderer/components/compute/SharedProjectPanel.tsx` | `486db6542b8bf6c440657060ab8f30f69d4f8d352bf49811e7e1f9096b860fcf` |
| `backend/api/routes_model_comparisons.py` | `d0e5222b97db5432edfd2ebbcacf307af5bdbd5bb6b35896f9a2983c34e1492a` |
| `backend/api/routes_training_workspace.py` | `6420fb5b503dd72290be29e8333f2d4db795803f36637cdeca34d241019de7f0` |
