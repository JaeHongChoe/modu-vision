# GUI execution evidence and CI selection correction

GUI evidence now requires the exact case and a retained execution receipt.
A spec file alone cannot verify a GUI action. The receipt binds one expected
passing attempt on clean source, current test bytes, the actual platform and
report/harness/screenshot hashes. Skips, retries, expected failures, dirty source,
changed tests, linked files and foreign artifacts refuse verification.

`scripts/gui_execution_receipt.py` collects one explicitly supplied owned run.
It checks the recorded commit's Git test bytes, never starts an app or test,
and never decides human quality, independent review or feature acceptance.
The registry gate verifies the retained receipt and current test bytes.
An authored controlled report can exercise parser rules; it is not an actual
GUI run or a trusted independent acceptance decision.

The retained actual development Electron case on clean published e54d179 trains
an authentic local DINOv3 parent and candidate, opens the frozen manual-review
record and reloads its persisted state. It refuses inadequate synthetic quality
samples. This case does not establish F109's entire automatic-promotion flow.
The 156-feature action ledger remains incomplete.

The e54d179 Linux CI run37455205183 failed before CPU execution because two
operations test paths did not exist. Both paths are corrected. A regression
now checks every explicit pytest path and selector in Linux steps and Windows
matrix values against actual files and AST test declarations.

Verification:134 GUI/reference/collector/CI contract cases and8 CI-selection
cases pass without skips. All2,142 currently selected CPU tests collect.
Collection is not execution; full hosted CI must pass separately. Parent counts
remain64 software verified/18 pending. Exact file, JUnit, collection and actual
GUI receipt hashes are in the paired verification receipt.
