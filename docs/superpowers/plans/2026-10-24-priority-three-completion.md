# Original three priorities: remaining implementation

**Goal:** Finish the existing easy-use, assisted labeling and subscription-backed text agent scope before requesting actual account participation.

**Architecture:** Keep the current main-owned SIWC protocol/controller/store and bounded renderer tool runtime. Add explicit account recovery and consent controls; keep credentials and provider requests in main. Use actual proposal/adoption stores for a controlled labeling composition scenario.

**Spec:** `docs/product-direction-easy-labeling-agent.md` sections 3, 4, 9, 14 and 15; the user's existing instruction to complete all three priorities.

**Constraints:** No real login, credentials, plan inference, image transfer, training or model reruns. Preserve source images, confirmed labels, review/fingerprint guards, other checkouts and active remote CI. Keep recurring monitoring stopped. Do not qualify changed code with the previous head's results.

## Tasks

- [x] TDD: explicit plan reconsent, durable first-use acknowledgement and account-specific usage pause with user-controlled resumption; safe recovery diagnostics.
- [x] TDD: main-owned explicit stale-store recovery. New leases carry a PID and random nonce. Only a provably absent process and a held Electron single-instance lock permit recovery; live/unknown/legacy owners remain locked. Bind confirmation to the exact files, archive encrypted state, discard uncertain credentials before releasing the stale lease, retain registration and local preferences.
- [x] Localize UI-reaching refusals in dataset metadata/export without changing status, payload shape or machine comparison tokens.
- [x] Verify the controlled 20-label to 50-draft to reviewed-label to warm-start request composition; correct unproven accuracy wording.
- [x] Exercise actual Electron main, protected store, loopback callback, preload and renderer tools together with generated tokens and fake provider transport only.
- [x] Run affected tests, renderer suite, type checks, production build and evidence checks; independent review.
- [ ] Publish the integrated follow-up and qualify its exact head before merge.

## Recovery review focus

- A renderer or agent cannot unlock the store without an explicit current confirmation.
- Live owners, unknown PID results, legacy nonce-only locks, unsafe links/permissions and changed files fail closed.
- A crash during recovery never makes an uncertain old refresh token usable by a new owner.
- Archive remains encrypted and private. API-key configuration, data and models are unaffected.
- If another legitimate owner acquires the cleared store first, recovery does not overwrite it.
