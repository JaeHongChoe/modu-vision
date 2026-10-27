# Browser host and reviewed extension contracts

## Browser Studio

Serve the built renderer and the authenticated API from the same HTTPS origin.
`npm run web` builds the current source into its separate browser data root,
configures the exact origin, and runs direct loopback-only Uvicorn TLS. Its first
administrator is provisioned in the terminal through AccountStore before serving.
See [local browser setup](local-browser.md) for certificate, data and OS limits.
A manually hosted shared server instead configures `--shared-auth-dir` and the
exact `MODU_BROWSER_ORIGINS` before startup, and provisions administration outside
the browser with its server process capability. A browser cannot bootstrap administration.
The localhost launcher additionally owns a windowless native helper and a remote-connection
broker. Dedicated broker login keeps upstream credentials in server memory; generic
shared-server installations do not gain this capability.

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
| Server-side source path | Localhost native chooser stages a project-owned copy; typed path requires explicit project-member sharing | Explicit path or native picker |
| Local file/folder opening | Approved localhost helper; current authorized project only | Native bridge |
| Desktop updates and OS service registration | Localhost helper and existing OS/signature/admin/owner checks | Separate authorized native operation |
| Telemetry | Launcher forwards `/ws/` directly to the authenticated ASGI app over WSS | Native authenticated connection |

Local host HTTP calls require the exact HTTPS origin, a valid HttpOnly account cookie,
CSRF for unsafe methods and a server-issued tab. Native approval is separate from account
login. Selected paths are short-lived, one-use and bound to actor/session/tab/workspace/
project/role/membership revision/labelset plus file identity and a fixed operation. Standard
shared-server external-path denials remain in place. No browser-supplied shell, arbitrary RPC
method or browser preload shim is accepted.

Restored projects retain manifest/model/labelset IDs. A distinct registration/workspace key
separates copies and their account memberships, jobs and artifacts. Logical-ID queries use
one validated location key for both permission checks and row access; exact registration
queries cannot borrow another location's authority. Registration inode checks precede legacy
normalization. Backup/restore receipts preserve published output after an interrupted reply;
recovery never blindly repeats a filesystem publication.

Remote HTTP/assets/WSS use an opaque connection reference bound to local session/tab,
upstream origin/account and an authority epoch. This reference is not a bearer capability.
Local cookies/CSRF and upstream Set-Cookie are not relayed. Current remote context headers
are distinct from local native context. Login/context/logout/revocation reserve the new epoch
before awaiting cleanup, and only admitted WSS streams release their owned capacity slot.

For an empty browser project, open **검증된 데이터 버전** in Step 1, choose a
ZIP and use **ZIP 올리고 검증**. Validation and **이 버전 채택** publish the
reviewed dataset index; they do not change the working source. The separate
**이 ZIP으로 작업 시작** action selects the accepted upload as this project's
source and loads its images for labeling, training and evaluation. It requires
existing `project.manage` authority, fresh project/actor/revision/source checks,
and a matching source/task/labelset precondition at the source write. It never
uses a browser PC path as a server path. A refused write keeps the prior saved
source; a newer project view is not rolled back.

Classification changes save through the image class picker; geometric labels
use **변경 저장**. Save a train/validation/test split before training. Training
and evaluation run on the backend server with that server's model/runtime and
weights, not inside the browser. After reload, log in again and use **작업 센터**
to reopen the original completed job and its saved evaluation. Source restoration
must finish before connection or project-switching controls are enabled; this does not bypass the
existing in-flight-work or unsaved-edit checks.

`browser-team-session.spec.ts` exercises a real isolated API behind an owned
loopback HTTPS proxy and the built renderer. Its self-signed certificate is a
private test fixture, not production certificate provisioning. Development
Electron and Windows install qualifications remain distinct.

`browser-upload-lifecycle.spec.ts` covers the empty-project upload/source handoff.
`browser-upload-annotation-reconnect.spec.ts` saves an actual canvas bounding box
and reopens it after login. The opt-in `browser-training-reconnect.spec.ts`
(`@owned-model`) performs one real ResNet18 epoch and held-out evaluation, then
reopens the same job after reload, a new browser session and an owned backend
restart. Set `MV_E2E_RESNET18_WEIGHTS` to an existing
`resnet18-f37072fd.pth`; the test verifies its hash prefix and copies it into the
isolated backend cache. It neither fabricates a checkpoint nor downloads model
weights. These are synthetic functional checks, not field-quality or installer
acceptance. Standard evaluation uses the backend's automatic device selection;
the CPU training setting does not force its evaluation device.

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
