# Browser host and reviewed extension contracts

## Browser Studio

Serve the built renderer and the authenticated API from the same HTTPS origin.
Configure `--shared-auth-dir` and the exact origin in `MODU_BROWSER_ORIGINS` before
starting the server. Provision the first administrator with the server process
capability, outside the browser. A browser cannot bootstrap administration or
open another server's account session. Cross-origin account connections use the
desktop client instead.

The browser logs in with `transport: cookie`. The session cookie is Secure,
HttpOnly and SameSite=Strict. The password and CSRF proof are not persisted by
Studio. Only unsafe requests to that same server's `/api/` routes receive the
in-memory CSRF proof. Reload requires a new login; explicitly disconnect to
revoke the server session. A closed tab does not immediately revoke its cookie.
Project authority remains the authenticated account and committed workspace,
project and actor context. Revoked memberships and expired sessions remain
server-side refusals; the browser cannot grant itself permissions.

| Capability | Browser | Desktop |
| --- | --- | --- |
| Authorized project and saved-flow view | Same-origin HTTPS account session | Main-process account session |
| ZIP upload and artifact download | Browser file selection and downloads | Same API contract |
| Server-side source path | Explicit server path; cannot access browser PC folders | Explicit path or native picker |
| Local file/folder opening | Unavailable, with an explanation | Native bridge |
| Desktop updates and OS service registration | Unavailable | Separate authorized native operation |
| Telemetry | Server must proxy `/ws/` with trusted HTTPS/Origin handling | Native authenticated connection |

`browser-team-session.spec.ts` exercises a real isolated API behind an owned
loopback HTTPS proxy and the built renderer. Its self-signed certificate is a
private test fixture, not production certificate provisioning. Development
Electron and Windows install qualifications remain distinct.

## Extension admission

`backend.contracts.extensions.ExtensionManifest` is declarative metadata. It
cannot contain a Python entrypoint, shell command, installation command or
arbitrary runtime loader. The maintainer supplies the reviewed source digest,
permission grants and approved license identifiers separately to
`admit_extension`. A manifest cannot assert its own review or license approval.
Source changes, unknown protocols, invalid versions, unapproved licenses,
ungranted capabilities and capabilities incompatible with the adapter kind
are refused.

| Adapter kind | Contract boundary | Possible permissions |
| --- | --- | --- |
| ModelAdapter | Reviewed model stage, explicit device/input/output contract | `model.infer`, `source.read` |
| InputAdapter | Validated input envelope, explicit ownership and coordinates | `source.read` |
| DeliveryAdapter | Idempotent result delivery and acknowledged receipt | `result.write` |
| StorageAdapter | Scoped content-addressed artifact read/write contract | `artifact.store` |

For a contribution, implement the adapter statically in its existing production
boundary, add a contract fixture that exercises its accepted and refused inputs,
and submit source and notices for maintainer review. Pin the reviewed source
SHA-256, protocol 1 and a concrete version. Admission returns
`reviewed_static_adapter_only`; it does not install, import or execute code and
never changes a model's verified device/runtime support state. Model execution
still goes through the production catalog, preflight and evidence checks.

The admission fixture covers all four kinds and validates source, permissions,
version and license refusal. It is not a plugin sandbox, automatic plugin
installer, external-contributor review or model-quality approval. These claims
require separate evidence.

`examples/extensions/fixture_camera.py` is a static MIT camera adapter example.
Its reviewed factory returns a finite in-memory simulator through the existing
CameraAdapter boundary. `create_reviewed_camera` verifies the factory's exact
source file/hash and admission before opening the source. The fixture exercises
read, exhaustion and release; it does not qualify a physical camera or dynamically
import a contributor's code. Maintainers review/import contributions explicitly.
The browser HTTPS fixture does not proxy WebSocket upgrades: its cookie/API/flow
and transfer evidence does not qualify browser telemetry or production TLS.
