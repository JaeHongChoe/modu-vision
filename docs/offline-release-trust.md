# Offline release trust and recovery boundary

Packaged Studio requires a maintainer-provisioned `release-trust.json` inside
the signed application resources. Missing trust fails closed. A downloaded
manifest cannot install its own root key or broaden permissions. Provision and
rotate roots through an independently verified signed application release.

The root contains schema version 1, one native publisher identity, an ID-to-key
map (base64 DER SPKI Ed25519 public keys), revoked key IDs, exact HTTPS origins,
and the installed `api_context`, `worker`, `runtime`, `dataset_index` compatibility
matrix. Keep key IDs stable; retain historical revocations. Revoked and unknown
keys are refused. Root reading is bounded to 32 KiB and checks the opened file's
identity before and after reading. Private signing keys are never packaged.

An envelope has exactly `schema_version`, `key_id`, `payload_b64`,
`signature_b64`. Sign UTF-8 JSON with recursively sorted object keys and no
whitespace; arrays keep their order. Payload and signature are bounded and
duplicate/noncanonical fields are refused. The payload declares version,
channel, platform, architecture, HTTPS installer URL, size/SHA-256, publisher,
the exact compatibility matrix and every installer/runtime-pack artifact.
The native signature publisher must equal both the installed application's
publisher and the pinned root publisher. Hash verification alone is insufficient.

An offline bundle contains `release.json` and `artifacts/`. The latter must
contain exactly the signed flat inventory, with no additional, missing, linked,
ambiguous or reserved-name files. Files are read through bounded handles and
checked for replacement or growth. The installer is copied to owned private
update storage and rechecked after native signature verification. Optional
packs are verified, but are not automatically copied, loaded or installed.

In **Packages, devices and diagnostics → Installation and diagnostics**, choose
**Offline package verification**. Browser Studio cannot open the native picker.
Development Electron refuses before the picker because it has no authenticated
installed publisher. The operation records a durable delivery journal. A crash
retains interrupted/unverified state and preserves the existing executable and
projects. It performs no extraction, installer launch or database migration.

Stable releases refuse prerelease versions. Every normal delivery refuses a
downgrade. An exceptional recovery rollback requires a separate reviewed
record; this verifier does not implement or silently authorize that exception.
Back up the project, drain training/inspection, and verify a safe execution
window before manual installation. Existing project/global migration tools
support their declared owned/drained scopes only. An application installer
cutover with live jobs, cross-schema inverse migration or post-cutover forward
recovery remains unqualified. After new writes, restoring an old snapshot must
not discard those writes.

For a supported owned/drained control-store restore, the global migration CLI
records the sealed recovery generation and next fence before switching its
pointer. If interrupted, explicitly repeat the same migration identity with
`recover --action restore`. It reuses that recovery generation and checks its
original backup, source and target hashes. New writes or changed authority
refuse replay and require forward recovery. This DB replay does not install an
application, supply publisher trust or qualify Windows power-loss recovery.

No publisher identity, signed release installer, native installation receipt
or production root is supplied by the contract fixtures. S6-04/S6-06 remain
pending for those gates. Do not publish a signed/offline installation support
claim based on the development controls.
