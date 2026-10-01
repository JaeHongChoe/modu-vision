# Compatibility and configured delivery

## Implemented scope

- Project schema is checked before labelset creation, relocation, activation, or manifest writes. Unknown schemas are rejected. Legacy manifests without a schema normalize to schema 1 after an exact original-byte backup and durable hash-bound receipt in `.migrations`. Unknown manifest fields survive relocation and updates.
- Compatibility preview performs no project writes. Apply checks the preview hash, is idempotent, rolls a failed manifest publication back when its own output still matches, and finalizes an interrupted receipt only when the current bytes match its normalized output. Later edits are never overwritten during recovery.
- Activation errors leave the prior active project intact. Required legacy copy failures are surfaced. Project archive restore bounds manifest size, reports malformed archive errors consistently, retains original migration backups unchanged, and discards copied native installation files and ownership/process tokens.
- Desktop version comes from Electron. Backend readiness records its actual version source separately. Main-process native signature inspection distinguishes publisher verification, unsigned/ad-hoc artifacts, invalid signatures, unavailable tools, and development execution.
- The update channel is explicitly configured in private app user storage. No default server, signing identity, certificate, or key is supplied. HTTPS manifests are limited to 64 KiB; channel, semantic version, platform, architecture, same-origin package URL, size, and SHA256 are checked. Packages are limited to 1 GiB and streamed into owned temporary files. Manual delivery requires matching bytes and reports native signature and publisher comparison independently. Automatic installation remains false.
- Native startup descriptors resolve the current applied approved release through a trusted bootstrap, validate its package/policy/device, and load the token from private project state. Descriptors contain neither the token nor a pinned package. macOS uses a durable user LaunchAgent; Linux uses systemd user units; Windows uses per-user logon tasks. Installed and running state are separate from inspection runtime readiness and platform acceptance. Removal failures retain owned recovery records.
- Frozen backend CLI dispatch supports managed startup and direct inspection startup. Runtime process ownership recognizes this dispatch while retaining the exact state directory and command identity checks.

## Interfaces

Existing project router:

- `POST /api/project/compatibility/preview`: `{project_dir}` → schema, compatibility, migration requirement, manifest hash, status.
- `POST /api/project/compatibility/apply`: `{project_dir, expected_manifest_sha256}` → compatibility plus applied receipt/backup reference where applicable.
- Shared mode requires the exact selected project; schema mutation requires its owner. Local desktop mode can select an unopened project.

Main process IPC, restricted to the trusted application main frame:

- `distribution:get-status` / `window.api.getDistributionStatus()`
- `distribution:configure-channel` / `configureUpdateChannel({channel,manifest_url} | null)`
- `distribution:check-update` / `checkForUpdate()`
- `distribution:download-update` / `downloadUpdate()`

The manifest requires `version`, `channel` (`stable`/`beta`), `platform` (`darwin`/`win32`/`linux`), `arch`, `url`, `sha256`, and `size`. Download results expose byte integrity, signature evidence, publisher match, and manual-handoff readiness. They never execute an installer.

Existing runtime service APIs keep `/install`, `/install/activate`, and `/install/remove`. Service state adds `native_install`: platform/kind, tool availability, prepared/registered/enabled/running state, startup scope, prerequisite, and `verified: false`. Windows task process observation is delegated to the separately checked runtime identity.

## Verification evidence

- Red regression: six initial schema/activation/archive tests failed before implementation.
- Red regression: startup tests and five initial distribution manager tests failed before implementation.
- Final affected backend batch: **78 passed** across project migration, native startup, managed service, project workspaces, delivery, archives, nested scopes, and labelset fingerprints.
- Follow-up native removal/error-state regression: **7 passed**, including the added Windows permission-denied removal boundary. This overlaps the prior batch and is not added to its count.
- Distribution and existing delivery renderer contracts: **13 passed**.
- Main/preload/renderer typecheck and production build passed in integration. Broad and later focused regression, package content hash checks and native observations are recorded in `TEAM-WORKFLOW.md`.
- Private macOS UI readback displayed Electron/backend version 0.1.0, current schema 1, an unconfigured update channel, unregistered startup and a failed native signature check. This confirms evidence is shown separately; it does not establish release signing or native startup acceptance.

## Remaining acceptance and prerequisites

- A real signing identity, signed artifacts, and the actual HTTPS release channel are external prerequisites. No signed-release or notarization acceptance is claimed.
- Native descriptors/commands were exercised with isolated mocked OS responses. No global startup registration was installed. Actual logout/login/reboot persistence and Windows/Linux execution remain pending platform acceptance.
- Linux boot before user login requires separately configured user lingering. Windows startup is a logon task, not an SCM service. ZIP artifacts require separate publisher signature verification. Unavailable verification stays visible.
- Frozen packaging resources/CLI are implemented, but no standalone executable was built here. Integration used a private unpacked macOS app with the configured development Python; its package content passed 40 integrity checks. This is separate from a signed distributable release.
- No distributable release was published by this increment. Git publication is recorded separately.
