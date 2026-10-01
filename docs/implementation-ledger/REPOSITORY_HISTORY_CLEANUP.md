# Repository history and branch cleanup

Date: 2026-10-01. Scope: an explicit user request to remove third-party company/product references from repository history and clean up merged branches.

## Verified changes

- Preserved all 33 existing main commits, author/committer identities and dates, parent order and graph structure while assigning rewritten commit identifiers.
- Sanitized four commit messages and historical text/file paths; removed two obsolete historical comparison documents.
- Scanned every main-reachable commit, blob and historical file path. Target company/product references: **0 matches**.
- Confirmed the complete tracked file tree immediately before and after rewriting was identical: `fd488c67b1444921cea99cf8332797e385323058`. Application source and current application assets were unchanged by the rewrite.
- Removed six merged local branches and three merged remote branches after checking they were included in main. Only `main` remains in both branch lists. The linked QA checkout remains available at a detached main commit so its local build artifacts are preserved.
- Published sanitized main at `ccd280f6f44bf3579a4423bbfb745164cb6c85be` with one atomic push, using explicit expected-head leases for main and the three deleted remote branches. Remote main and the branch list were read back independently.
- Retained a private, verified recovery bundle and an old-to-new commit map. Bundle SHA256: `7c15820547ba1e2ec92352cc96daf4f6bf3d09e8e970ae60f63e75cff720cec5`. The bundle contains the original history and is not part of the public repository.

This follow-up changes documentation and the program baseline commit reference to the rewritten identifiers. It adds one documentation commit after the preserved 33 commits. No application code or input data is changed by this follow-up.

## Follow-up expanded history audit

The initial naming scan targeted one benchmark vendor. Follow-up inspection found other historical comparison, UI-style and customer-sample names, so the history cleanup was expanded to those references and associated product labels. Custom anomaly folder-convention comments and simulated sensor pixel-pitch descriptions now use generic wording. Actual dependency, hardware, model-source and license references are preserved.

- Preserved the 34 commits present before this follow-up, including the first cleanup receipt, with authors, committer dates and ordered parent relationships unchanged.
- Scanned 34 commits, 1,538 reachable blobs and all 752 distinct historical paths. The expanded competitor/customer/comparison-product target set has **0 matches**.
- Confirmed the current tracked tree was unchanged by this rewrite: `239848f3862b1ff5a9355c1a0615067dbcf0b7f4`.
- Published the expanded sanitized history at `d438158fef28fb76ee020322d51e6d63533c3ef0` with an explicit expected-head lease; local and remote main matched on readback. Main remains the only branch.
- Retained another verified private recovery bundle before the expanded rewrite. SHA256: `4ea6a31c0c718dcbaf3c1dd0f077b5f1e36d8fa1c642bbce9c1a2974b0b8cdbe`.

This final documentation update adds one commit after those 34 commits and aligns the recorded commit references. Application code, model artifacts and input data remain unchanged. Earlier publication identifiers above are shown using their rewritten equivalents. The first-pass receipt's 33-commit count describes that earlier step.

## GitHub preservation limitation

Closed pull request #2 still exposes its original head-branch metadata and commit objects through GitHub's retained PR references and old SHA URLs. These are outside the current main history and branch list. This cleanup does **not** claim complete removal of every old GitHub reference or cache.

GitHub documents that pull-request references are read-only and that it does not remove non-sensitive data through its sensitive-data cleanup support process. See [GitHub's repository history cleanup documentation](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository). No repository deletion or recreation was performed.

## Other checkouts

A checkout made before this cleanup still contains the old commit graph. Start from a fresh clone for a clean history, or preserve its uncommitted work before aligning it to the new main. Merging the old graph back into main would restore the removed historical references.
