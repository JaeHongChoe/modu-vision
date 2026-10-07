# Keyboard activation during draft save

The retained failed Linux run37538406281 on0f3be05 had2307 CPU passes,
seven CPU skips,162 browser passes,one browser failure andone browser skip.
Its exact trace shows the fixed-ROI button disabled while an automatic draft
save ran. The test called `focus()` anyway: that call does not wait for an
enabled control. Focus remained on the image-picker opener and the next Enter
reopened that dialog instead of adding an ROI. The failure preceded the display
scale loop. The failed source and artifacts remain failed.

A shared keyboard test action now polls for a visible, enabled and actually
focused target before sending its key once. It retries only the focus
precondition. The product's save/edit locks and existing geometry assertions
are unchanged. A real browser with a controlled disabled target reproduces
the wrong-opener activation before this correction and verifies exactly one
target activation after release.

Clean committed sourcec37b1ba passed the controlled regression, the actual
browser canvas case and the actual macOS development Electron canvas case:
**3 passed,0 skipped,0 retried in34.7 seconds**. The canvas cases retained
keyboard add/connect/delete/undo/redo, dialog cancellation and focus restoration,
two window sizes, four display scales, original512x256-pixel ROI coordinates,
node movement and identical draft reopen. E2E TypeScript checking passed.

The seven historical CPU skips are two Windows file-sharing cases, one
case-insensitive filesystem case, one explicit OpenVINO-interpreter case and
three Apple OpenSSH-parser cases. The historical browser skip requires an
explicit opt-in for two CPU training runs. Eleven authentic-weight cases were
outside that public lane. These scopes are recorded as uncovered, not passed.

Root review is recorded. New complete published-source Linux CI, physical
devices, human quality, signing and final release acceptance remain pending.
Parent accounting remains66 software verified/16 pending, including the
user-waived Windows actual-QA item. The72-hour run continues independently.

Receipt: `docs/verification/receipts/2026-10-07-keyboard-saving-control.json`.
Three sanitized clean-source GUI receipts are linked by exact hashes there.

## Individual native control bindings

Nine reviewed actions add16 verified scenarios to F064 and U007: keyboard
inspection-node addition/selection, compatible and incompatible typed ports,
Delete/undo/redo, dialog focus/cancellation/local confirmation, fixed-ROI node
addition, scaled node drag, draft save/reopen, original-pixel ROI drag and arrow
movement/resize. Each action binds the exact c37b1ba native case, test/source,
manifest and screenshot hashes. Original image bytes and reopened draft SHA
were read back. The geometry case binds no learned model and executes no
inspection or deployment.

The expanded inventory has146 curated actions and264 verified scenarios.
Its758 pending scenario slots include newly enumerated controls; they are not
758 remaining parent jobs. Earlier action evidence and parent states are
preserved. Independent and full-feature acceptance remain pending.
