# Repository history and branch cleanup

Date: 2026-10-01. Scope: an explicit user request to remove third-party company/product references from repository history and clean up merged branches.

## Verified changes

- Preserved all 33 existing main commits, author/committer identities and dates, parent order and graph structure while assigning rewritten commit identifiers.
- Sanitized four commit messages and historical text/file paths; removed two obsolete historical comparison documents.
- Scanned every main-reachable commit, blob and historical file path. Target company/product references: **0 matches**.
- Confirmed the complete tracked file tree immediately before and after rewriting was identical: `fd488c67b1444921cea99cf8332797e385323058`. Application source and current application assets were unchanged by the rewrite.
- Removed six merged local branches and three merged remote branches after checking they were included in main. Only `main` remains in both branch lists. The linked QA checkout remains available at a detached main commit so its local build artifacts are preserved.
- Published sanitized main at `9a2d915af146321efe254722fb2aa1e994b6447e` with one atomic push, using explicit expected-head leases for main and the three deleted remote branches. Remote main and the branch list were read back independently.
- Retained a private, verified recovery bundle and an old-to-new commit map. Bundle SHA256: `7c15820547ba1e2ec92352cc96daf4f6bf3d09e8e970ae60f63e75cff720cec5`. The bundle contains the original history and is not part of the public repository.

This follow-up changes documentation and the program baseline commit reference to the rewritten identifiers. It adds one documentation commit after the preserved 33 commits. No application code or input data is changed by this follow-up.

## GitHub preservation limitation

Closed pull request #2 still exposes its original head-branch metadata and commit objects through GitHub's retained PR references and old SHA URLs. These are outside the current main history and branch list. This cleanup does **not** claim complete removal of every old GitHub reference or cache.

GitHub documents that pull-request references are read-only and that it does not remove non-sensitive data through its sensitive-data cleanup support process. See [GitHub's repository history cleanup documentation](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository). No repository deletion or recreation was performed.

## Other checkouts

A checkout made before this cleanup still contains the old commit graph. Start from a fresh clone for a clean history, or preserve its uncommitted work before aligning it to the new main. Merging the old graph back into main would restore the removed historical references.
