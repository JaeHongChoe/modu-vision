# Owned offline portable application updates

The explicit POSIX command coordinates a signed portable application with one
owned, drained global database generation. It preserves original directories,
staged applications, database backups and recovery journals. It never discovers
an installed home, changes operating-system registration, downloads dependencies
or converts a copied worker into an owned worker.

## Provisioned inputs

Supply an independently pinned Ed25519 publisher authority using the existing
`src/main/releaseTrust.ts` authority/envelope fields. The authority file hash is
an explicit input, separate from the release manifest. The release must match
the actual host OS/architecture, selected channel, current application version,
allowed HTTPS origin and the authority's protocol/schema matrix. Unknown,
revoked, altered or downgraded releases are refused before staging.

The offline directory must contain exactly the signed artifact inventory: one
portable ZIP installer and any signed runtime pack files. Runtime packs are
retained and hash verified; this command does not import or execute them.

The ZIP contains `portable-application.json` and exactly its declared regular
files. The portable descriptor has fields `schema_version: 1`, `version`,
`platform`, `arch`, `entrypoint`, and `files`. Each file has `path`, `size`,
`sha256`, and boolean `executable`. The entrypoint is a declared executable.
Links, special files, encryption, duplicate/case-conflicting names, traversal,
undeclared files and excessive inventories are refused. ZIP archives are bounded
to 1 GiB compressed, 4 GiB expanded and 10,000 application files. This format
does not install NSIS, DMG or a macOS application requiring framework symlinks.

The destination already has an explicit owned global descriptor, all seven
declared control scopes, matching account/context authority, and no active or
uncertain jobs/leases. `global_migration.initialize_owned` only initializes a
new empty directory; it must not be applied to an existing installed home.

## Installation and startup

Run `preview` with the same inputs as `install` to obtain a read-only review.
Pass its `plan_sha256` as `--expected-plan-sha256` when installing. The command
recomputes the plan under exclusive admission and refuses changed application,
authority, target, previous pointer or source database bytes. `--target-json`
accepts the same bounded target object as `--target-file`; `--use-owned-version`
derives the current version from the verified original installation intent.

```sh
python -m backend.engine.runtime_update install \
  --root /path/to/owned-installation \
  --bundle /path/to/exact-offline-artifacts \
  --envelope /path/to/release-envelope.json \
  --authority /path/to/provisioned-authority.json \
  --pinned-authority-sha256 <independently-pinned-sha256> \
  --target-file /path/to/target.json
```

The target file declares `platform`, `arch`, `channel`, `current_version` and
`origin`. The standalone backend exposes the same command after
`--offline-application-update`. A source command requires the dependencies
declared in `requirements.txt`; frozen builds require the cryptography import
diagnostic as well as the actual startup/restart checks.

After signature, membership, identity and checksum verification, the command
copies the artifacts into private owned staging, fsyncs them, persists an update
intent and blocks ordinary store attachment. Before the DB pointer changes,
the existing global migration persists its own preparation journal and reports
that exact identity to the application intent. The application pointer binds the
same database generation and fence. Only a committed pair releases admission.
Old store objects refuse access after the database generation changes.

`launch-plan` re-verifies the signed envelope, pinned authority and every installed
application byte/mode, checks the committed app/DB pair, and returns the exact
`argv` and required `VISION_AI_STUDIO_USER_DATA_DIR`. The host launcher must retain
the owned installation's admission and apply that environment at launch. This
command returns a plan; it does not automatically execute the application or
grant native publisher, known-image or model-quality acceptance.

## Interrupted installation and later writes

`inspect --root ... --authority ... --pinned-authority-sha256 ...` reads the
current application/database pair without repairing it. The external authority
must match before inspection follows the intent's stored paths. The response
distinguishes a ready installation, committed pair and interrupted transition,
and lists the currently permitted recovery actions. An optional authority and
pin on `recover` bind recovery to this same inspected intent.

```sh
python -m backend.engine.runtime_update recover \
  --root /path/to/owned-installation --intent <update-id> --action finish
```

`finish` resumes only the same prepared migration/application and is idempotent
for its currently committed pair, including later normal writes. It refuses a
stale update without blocking the newer application. A pre-database `abort`
keeps the original pair and all staged artifacts. Once database preparation
starts, an abort cannot silently restore old application/database authority.

`forward` preserves new writes by taking a fresh, drained current-schema global
generation. It accepts only this update's current database generation and
retains the original migration identities in recovery history. Interrupted
forward recovery uses the same durable preparation path. An inverse or version
rollback is unsupported here and requires a separately approved conversion.

## Explicit desktop review

The installation/diagnostics panel can select a separate owned POSIX portable
home. It refuses the current application and current user home, including any
overlapping parent or child. Before a native folder picker opens, the packaged
host verifies both application and frozen-backend signatures against the fixed
publisher authority in its resources. An unsigned development app is refused.
The user cannot select a replacement trust authority through this panel.

Review shows the versions, publisher, inventory sizes and hashes. Confirmation
that the selected installation is drained and backed up enables the apply or
recovery button; the backend still verifies drained state independently. A main
process review ID is consumed before installation begins. Lost responses require
an explicit state read, never an automatic retry. Recovery also rechecks the
installation and update IDs shown in the selected view before it runs.

This panel does not launch the updated application or adopt an installed home.
The selected app must subsequently be started and qualified with a known image.

## Qualification boundaries

Controlled Ed25519 keys, tiny portable executables and subprocess power-loss
fixtures verify the software transaction. Browser review/recovery uses the real
main manager and Python transaction with controlled native-signature outputs;
actual Electron verifies the unprovisioned development refusal. These controls
do not qualify a real publisher, a signed native positive installation, an
installed customer home, physical power loss, native code signing, a model's
quality, or Windows. Actual frozen CLI execution,
Linux CPU execution and the precise source/resource hashes are recorded
separately. Those remaining requirements stay pending in the service program.
