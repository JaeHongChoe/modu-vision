# Public candidate decision

Current disposition: hold until the applicable gates have actual evidence.
This checklist does not publish a release or authorize production changes.

| Gate | Required evidence |
| --- | --- |
| Source and build | Exact source SHA, runtime/lock inventory, SBOM and delivered artifact hashes |
| Functional coverage | Requirement and incumbent-feature evidence; skips and exclusions recorded |
| Correctness/security | Fresh stale-approval, verdict parity, data-loss and authorization regressions; no unresolved critical defect |
| Model/flow | Exact reviewed cohort, calibration, checkpoint, graph, rules and package binding |
| Target | Actual supported OS/device/runtime execution and package parity; documented unqualified combinations |
| Upgrade/recovery | Interrupted download/install/migration, preserved data and verified recovery after new writes |
| License | Authorized distribution decision and matching notices for every artifact scope |
| Publisher | Final-byte signature and publisher identity; unsigned development separated from stable/beta |
| Pilot | First-use participant record and re-executed critical blocker fixes |
| Maintenance | Owner, support/deprecation policy, redacted diagnostics and recovery practice |

Use `scripts/check_service_plan.py`, `scripts/distribution_release_gate.py` and
`scripts/release-readiness.cjs` for their declared checks. Their passing results
are individual inputs, not a complete public-release decision. Windows actual
tests were waived for current development; this does not turn them into verified
Windows support. A release decision must retain every unresolved or excluded
condition, its effect on supported scope and the responsible reviewer.
