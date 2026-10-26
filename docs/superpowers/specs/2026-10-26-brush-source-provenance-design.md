# Current brush-code test provenance

PR12's product regression changes routes_dataset.py. Eight current browser specs
still pin its old SHA, producing nine hosted failures. Existing historical-source
declarations already preserve these specs' executed ancestor bytes and receipts.

Controller approach: evolve only the route pin in those current specs and the
17 existing declarations' current_test_source_sha256/reason. Keep every receipt,
receipt SHA, original execution source/test/original-spec SHA and ancestor anchor
unchanged. Current browser cases must run again with all existing controls.

Alternative new current-spec copies would require selection/schema changes and
offer no benefit. Product indirection to keep an obsolete route SHA, removing
guards or rebinding historical acceptance would be incorrect.

The migration changes current test provenance only and supplies no new human,
native or historical GUI execution credit. QA wording must distinguish retained
historical bytes from evolved current spec bytes. Existing checker/history schema
and all tamper refusals remain unchanged.

The adjacent local current-summary lifetime test has a separately reproduced
pre-existing exact-response expectation that omits four additive API fields.
Bring that expectation up to the actual reviewed contract without changing the
API, removing full-response equality or relaxing any raw9/lifetime/drain checks.
The earlier failure and old-framework compatibility error remain historical.
Keep the new mask/reopen and saved-summary regressions permanently selected by
the existing full CPU lane; add only these three file references, preserving all
existing references, timeouts, concurrency and model exclusions.
