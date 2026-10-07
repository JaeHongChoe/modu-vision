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
to 1 GiB compressed, 4 GiB expanded and 10,000 application files. The separate
schema2 darwin layout supports one canonical `.app/Contents` tree and its signed,
bounded internal Framework links. Neither format invokes an NSIS or DMG installer.

The destination already has an explicit owned global descriptor, all seven
declared control scopes, matching account/context authority, and no active or
uncertain jobs/leases. `global_migration.initialize_owned` only initializes a
new empty directory; it must not be applied to an existing installed home.

## Explicit original installed-home adoption

An original current-schema macOS/Linux installation can acquire the owned
descriptor through a separate reviewed adoption. Supply the canonical absolute
root and a scopes file containing exactly:

```json
{
  "ledger": "jobs/ledger.sqlite3",
  "leases": "resource_leases.sqlite3",
  "profiles": "compute_profiles.json",
  "accounts": "auth/accounts.sqlite",
  "context": "projects/.context.sqlite3",
  "local_journals": "local_jobs",
  "remote_journals": "remote_jobs"
}
```

Read the source without creating source locks, changing schemas, rebinding
accounts, clearing sessions or launching workers:

```sh
python -m backend.engine.global_migration preview-installed \
  --root /explicit/original-installation --scopes-file /review/scopes.json
```

The preview binds root device/inode, original file bytes/inodes/modes, current
schemas and account/context/project authority. Qualification is bounded to
10,000 files/directories, 512 MiB per file, 2 GiB total and 10,000 records per
scope table. Logical SQLite/history validation uses finite private snapshots
read through stable, nonblocking, no-follow file descriptors; profile JSON is
parsed from the captured bytes. Malformed project objects, duplicate semantic
JSON keys, and changed identities/bytes refuse the preview.
Registered projects and their model paths must remain under this
same original root. Linked/hard-linked/shared-writable files, copied or existing
ownership markers, unknown schemas, external project paths, live or uncertain
jobs/leases/open attempts, and unverifiable ended journals refuse adoption.
All original sessions and pending OIDC exchanges must first be drained through
their original supported controls; adoption preserves their source bytes and
does not activate copied authority.

After independently establishing original ownership and stopping all writers,
create a separate attestation file containing the exact preview `root_identity`
and the literal booleans `owned_original: true` and `writers_quiescent: true`.
An old unowned writer does not participate in the new global lock; this explicit
operator attestation supplies quiescence, and no process is stopped or signalled.
Writers that can replace ancestor directories are outside this attested-quiescent
contract. Keep the entire original root and its ancestors under operator control.

```sh
python -m backend.engine.global_migration adopt-installed \
  --root /explicit/original-installation --scopes-file /review/scopes.json \
  --expected-preview-sha256 <independently-reviewed-preview-sha256> \
  --attestation-file /review/owned-quiescent-attestation.json
```

Under exclusive admission, adoption rechecks the exact source, retains a private
raw-byte backup and seal in `.installed-home-adoption`, then atomically publishes
the root-bound owner descriptor without replacing another descriptor. The
original stores and project files remain byte-identical. An interrupted backup
copy or sealed publication can retry with the same preview and attestation;
changed sources, foreign controls or altered backups refuse. A published exact
retry retains the installation identity. The existing `preview`/`apply` global
migration path then remains a separate explicit conversion.
Adoption, global `apply`/`advance`/`recover`, and the live cutover/recovery APIs require the application
launch lease guard to report quiescence; live or ambiguous application ownership
refuses mutation. Installations without launch controls retain their existing
offline behavior.

The CLI never discovers a user home, provisions a publisher, installs an OS
package or proves an old process has exited. This qualification uses disposable
POSIX fixtures; Windows native, actual installed customer-home migration,
publisher/native signatures, devices, model quality and independent acceptance
remain separate. An unknown control or interrupted atomic-control temporary
file is retained for explicit investigation rather than silently discarded.

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
Committed inspection shares the backend's live admission fence. A pending
transition still requires exclusive admission; a pending pointer appearing
before shared entry refuses readback. Inspection never drains or stops a writer.

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

After a committed transition, the panel can explicitly request **전환한 portable
앱 시작**. Trusted main chooses the pinned frozen controller, selected root,
installation/update IDs and database fence. Renderer input cannot select an
executable, arguments, environment, descriptor or process to stop. A legacy
backend without protocol1 and the four checksum-bound early handler sources
refuses before any unknown controller flag is executed. Installed-home adoption
remains a separate operator workflow.

The manager consumes a launch request before spawning. A bounded starting
acknowledgement confirms durable spawn publication, not successful inference.
If the response is lost, main closes only its own reply streams and never
signals the persistent controller or retries. **portable 앱 실행 상태 확인**
reads the original durable ownership. Reopening and reselecting a supported
committed installation restores that readback and disables update/recovery and
duplicate launch controls while ownership is unresolved.

## Persistent launch controller and early startup binding

The frozen entry dispatches `--owned-application-launch-controller` before
desktop backend imports. Trusted main supplies these exact inputs; `--inspect`
performs readback without reservation or spawning:

```sh
vision_ai_backend --owned-application-launch-controller --inspect \
  --root /path/to/owned-installation \
  --authority /path/to/provisioned-authority.json \
  --pinned-authority-sha256 <independently-pinned-sha256> \
  --expected-installation-id <installation-id> \
  --expected-update-id <committed-update-id> \
  --expected-database-fence <positive-fence>
```

The original controller retains its own database ownership handle and anonymous
private descriptor after the updater disconnects. A per-nonce transition mutex
permits claim/readiness/inspection alongside the backend's shared admission,
while reserve/cutover/recovery remains exclusive. Main authenticates before
instance-lock/user-home creation or mutable supervisor startup; the backend
authenticates before SCM, routes and recovery imports. Backend scopes are the
logical `root/projects` and `root/auth`; store resolution follows the committed
generation internally. Partial context, foreign artifacts, changed process
births, channel replay and missing descriptors refuse the binding.

Readiness is explicitly `authenticated_controller_binding_only`, based on a
separate durable receipt. The response's native-app, backend-native, actual
inference and release acceptance fields remain false. An ended direct child,
lost original controller or unverified descendant tree remains
`recovery_required`; no observation or PID lookup clears the original lease.
Do not delete launch journals or signal guessed processes to unblock an update.
Packaged descriptor retention, complete process-tree reconciliation, known-image
execution and OS installer registration still require their own qualification.

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
