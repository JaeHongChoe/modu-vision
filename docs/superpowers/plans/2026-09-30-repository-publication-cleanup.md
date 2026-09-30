# Repository publication cleanup

**Goal:** Publish project code and verifiable QA records without competitive comparisons, customer identities, private machine paths, or stale screenshots containing those details.

**Scope:** Preserve application behavior, technical dependency identifiers, required license attribution, and the distinction between feature QA and operational approval.

## Tasks

1. Back up every advertised remote ref into a private local mirror and verify a complete Git bundle.
2. Audit tracked text, filenames, commit messages, branch names, and screenshot pixels across reachable history.
3. Replace comparative prose with factual project documentation. Rename feature QA files and internal display identifiers. Remove obsolete screenshots containing external identities.
4. Integrate the latest feature fixes with current main locally; verify backend tests, typecheck, build, focused UI checks, and documentation links.
5. Rewrite a separate private mirror, retaining useful commit history while sanitizing text, filenames, commit messages, and branch names. Exclude the obsolete screenshots throughout history.
6. Verify all rewritten reachable heads, main tree parity with the tested source, and a complete old/new ref manifest.
7. Obtain explicit approval for replacing published branch histories. Publish only intended heads with expected-old-head leases; never publish backup or read-only review refs.
8. Verify published refs from a fresh clone. Document any hosting review/cache references that cannot be changed by a Git push and how existing clones should be replaced.

## Acceptance

- No audited external identities or private source paths in the intended published heads, their commit messages, or filenames.
- Required licensing remains intact; technical APIs and package names remain valid.
- QA claims retain their original scope and evidence.
- Main matches the cleaned, tested source, and a recoverable private backup exists.
- History replacement and hosting reference cleanup are reported as separate actions.
