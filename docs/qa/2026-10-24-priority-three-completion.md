# Original three priorities — implementation closure

The user's three priorities are easy use, less labeling work, and a bounded embedded agent using a ChatGPT account subscription. This follow-up completes software gaps found by reconciling those priorities with the original design and Claude handoff. Existing original-image, confirmed-label, fingerprint, label-set, truth-review and approval safeguards remain intact.

## Additional behavior

- Identity-only login has a distinct **ChatGPT 플랜 사용 켜기** action. It reuses the app's saved client and requests explicit consent; ordinary returning login does not force consent.
- Acknowledgement of the first plan-use welcome dialog persists per local registration, including logout/relogin and app restart.
- A specific plan-limit response persists an account-specific pause. The app stops new plan inference, displays usage management as the next action, and resumes only after the person opens usage management and explicitly permits requests again. Resumption sends no question automatically and does not claim the provider's limit has reset. API-key settings remain collapsed during that pause.
- HTTP status, response shape, request ID and allowlisted provider codes/request-field paths remain available in Korean technical guidance. Arbitrary provider body/code/param content is not copied to the renderer. Generic admission 401 does not delete credentials; confirmed terminal refresh failure and uncertain rotation remain fail closed.
- New store leases include a PID and random nonce. Recovery is offered only under the app's current single-instance authority and when the OS proves that PID absent. It requires a current file-bound ticket and a second explicit confirmation. The old encrypted file is backed up, uncertain credentials for all registrations are cleared while the old lock still blocks competitors, and only then is the lease released. Registration, selected model and local welcome/pause choices remain. Active, unknown and legacy nonce-only owners are never guessed dead. No remote revocation is claimed.
- Primary dataset-metadata/export refusals now explain the reason and next action in Korean. Existing status codes, payload shapes, validator/role/fingerprint logic and pinned machine-comparison tokens remain. Dynamic messages from shared engines/specialist modules are outside this bounded copy sweep.
- Quick retraining now describes making a candidate from newly reviewed labels rather than promising an accuracy improvement.

These choices follow the current official [sign-in/recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery) and [plan-use UX](https://developers.openai.com/siwc/ui-ux-guidelines) contracts. The application's ChatGPT path remains text-only; the existing API-provider image path still requires a separate one-image confirmation.

## Verification

- Renderer suite **2005 passed, zero failed/skipped** (41.2 seconds) after the final paused-state UX correction. A preceding run's only failure was the generated light-theme occurrence counters; regeneration changed comments/order, not colors. Renderer/main and E2E type checks pass. The final native build compiles production main/preload and renderer successfully.
- Subscription/IPC/UI/lifecycle focused group **109 passed**; the subsequent small paused-state component group **27 passed**. Store tests **20 passed**, including actual independent-process exclusion and exited-child recovery.
- Korean/request checks **67 passed**; metadata/export compatibility checks **141 passed**. Runtime precision checks **2 passed, 2 skipped** because native OpenVINO qualification is unavailable. These are bounded suites, not an assertion that every backend test passes.
- Independent security review found unsafe arbitrary diagnostics and invalid recovery discarding an in-flight refresh's ownership. Both were reproduced with failing tests, fixed, and independently rechecked. Final bounded review found no remaining actionable issue in those paths.
- The existing ChatGPT browser setup case passes **1/1** (12.8 seconds including the harness; 7.4 seconds in the case) with the updated bridge and welcome dialog contract. Its first local launch stopped at preflight because system `python3` lacked FastAPI; explicitly selecting the existing project venv resolved the fixture environment without installing or changing packages.
- Both new backend controls are included in the full CPU lane and therefore its related fast selector. CI source/selection checks pass **27/27**; the companion CI/service contract groups pass **30/30**. Extending the selector list initially tripped its exact-command guard. The guard now separately proves all previous command bytes remain unchanged after removing exactly the two additions, and pins the newly extended command. Time bounds, previous selectors and browser commands are preserved.

## Actual native integration

`scripts/qa/chatgpt-native.cjs` passed with the production main/index, native BrowserWindow, IPC, sandboxed preload, built renderer and owned backend. Only HTTPS provider replies/browser opening were replaced; no `window.api` shim was installed. Generated RSA-signed tokens and mock keychain were explicit test conditions.

The real callback verified PKCE and registered client binding. An identity-only grant disabled sending; explicit reconsent enabled plan access; the first-use dialog was acknowledged and a listed model selected. Namespace calls read status and filled a split without saving. Rejecting the split kept its original file hash; confirming it saved actual **14/4/2** assignments and persisted refused/confirmed tool records. Original 20 image hashes were unchanged.

The controlled limit paused new questions. A new process restored the acknowledgement and pause. Opening usage management and permitting resumption caused **zero automatic provider calls**. A further native restart with a retained dead-owner lease verified cancel left bytes intact; confirmed recovery produced an identical encrypted archive, cleared credentials and kept registration/model/acknowledgement. The three owned backend PIDs exited and all three ports closed. Process receipts recorded zero external provider-network attempts and zero generated-credential log attempts. Screenshots were visually inspected.

Earlier native fixture attempts remain preserved: two queued the next reply before the previous conversation finished; another used Playwright's Electron evaluate argument incorrectly. The fixture now waits for each distinct terminal response and uses the correct evaluator signature. These were fixture failures, not product successes or model-quality evidence.

## Labeling composition

`backend/tests/test_labeling_loop_composition.py`: **1 passed in 7.91 seconds**, with real project/storage middleware, routes, proposals, explicit reviews, 50 backups/origins, immutable training preparation and warm-parent binding. Only inference and worker submission are controlled; the checkpoint contains an explicitly untrained tensor fixture.

It prepares 20 drawn labels, creates 50 pending drafts without changing labels/masks, requires explicit review, retains the old model marked changed, and submits a new 70-label version with the correct parent SHA and five epochs. All original 70 image hashes remain unchanged. IPv4/IPv6 connections are forbidden. Existing quick-retrain component checks also passed **2/2**. This proves software composition, not learned quality or human labeling speed.

## Remaining external acceptance

Actual user login/terms and account eligibility, subscription inference/model/limit/refresh/revocation, real-key/paid/image-transfer consent, signed package/other OS qualification, novice timing and representative field-quality approval are distinct from implementation. The previous real-image diagnostic remains a quality failure for its tested configuration; this follow-up does not improve or approve that model.

The final commit requires its own exact-head hosted fast/full regression and merge qualification. Earlier heads' CI is not reused. The stopped 15-minute monitoring automation remains stopped; no deployment, service registration or production-quality approval occurred.

## Inherited full-CI fixture repairs

After the implementation was published as `26daaee0`, older full run `40060361301` reported one source failure and three browser failures in paths unchanged by that implementation. Its failures were retained, and merge qualification was held. The implementation's fast run `40067624247` passed all 11 jobs; that result does not qualify the subsequent test repair commit.

- Two mask-preflight specs retained stale hashes for `DatasetWorkflowPanel.tsx` and `routes_dataset_metadata.py`. Reviewing those source differences found only navigation attributes and Korean refusal text, respectively. Exactly four hash literals were refreshed; the scenario assertions and original receipts were not rebound. Both browser cases passed (17.5 seconds). Historical executed-test declarations retain the old receipt identity.
- The DICOM explicit PNG observer could see an earlier canvas image request finish during its own read. It now records but does not credit PNG responses whose original Request was not observed after this action started. A second owned request still refuses the capture, and POST identity checks, the original ten-second deadline, complete response bodies, context, pixels, hashes and abort checks remain. The actual observer regression went from 6 passed/2 failed to **9/9**; the real browser case passed (14.1 seconds including harness, 10.0 seconds in the case). Its receipt retains seven captures, three PNG/RGB proofs, two thumbnails, one original abort, 165/165 FDs and zero remaining handlers/processes/page errors. The local browser did not reproduce the overlap; the deterministic checks exercise both failing event orders.
- The replayed managed-drain fixture raced recovery publication with the unchanged backend ownership guard and its ordinary-stop marker. A test-only profile boundary now holds immediately before that original guard, within the same original deadline, until both observations exist. The production Python lease reader validates the pointer/hash; Python canonicalization compares the candidate copied from the original raw journal without JavaScript float re-encoding. The original guard executes without a wrapper, the ordinary-stop assertion stays unconditional, and the exact stderr allowlist is unchanged. The three fixture controls and original replay passed **4/4**; the entire owned quiescence module passed **43/43** (139.93 seconds, one existing dependency deprecation warning). Independent review found no production change or weakened guard.

The complete E2E harness group passes **64/64**, E2E types pass, and no model execution/training, native provider call or original-data change was needed for these test-only repairs. The previous native integration receipt remains tied to its original production-source hashes.
