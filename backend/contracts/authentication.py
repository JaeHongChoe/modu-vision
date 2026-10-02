"""Typed authorization decisions and an opt-in trusted identity-provider boundary."""
from dataclasses import dataclass, asdict
from typing import Protocol
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str
    actor_id: str
    action: str
    workspace_id: str
    project_id: str | None
    role: str | None
    membership_revision: int | None
    resource_revision: str | int | None = None
    resource_revision_verified: bool = False

    def model_dump(self):
        return asdict(self)


@dataclass(frozen=True)
class VerifiedIdentity:
    """Only a configured provider's verifier may construct this result.

    The adapter MUST verify signature, issuer, audience, expiry and nonce using
    a maintained OIDC library. These fields are never accepted from an HTTP body.
    """
    issuer: str
    subject: str
    audience: str
    expires_at: float
    nonce: str


class TrustedOidcProvider(Protocol):
    def authorization_url(self, *, state: str, nonce: str, code_challenge: str,
                          redirect_uri: str) -> str: ...
    def exchange(self, *, code: str, code_verifier: str, nonce: str,
                 redirect_uri: str) -> VerifiedIdentity: ...


@dataclass(frozen=True)
class OidcRegistration:
    issuer: str
    client_id: str
    redirect_uri: str
    adapter: TrustedOidcProvider


def configure_browser_origins(app, origins):
    """Call before serving; returned kwargs configure the outer CORS middleware.

    Authentication checks this exact allowlist even without a CORS middleware.
    Credentialed browser sessions exclude file/null, wildcards and HTTP origins.
    Desktop/SDK bearer transport remains independently authenticated.
    """
    checked = []
    for origin in origins:
        parts = urlsplit(origin)
        if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                or parts.path or parts.query or parts.fragment or origin != f'https://{parts.netloc}'):
            raise ValueError('Browser origins must be exact HTTPS origins')
        checked.append(origin)
    app.state.browser_origins = frozenset(checked)
    return {'allow_origins': checked, 'allow_credentials': True,
            'allow_methods': ['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
            'allow_headers': ['Content-Type', 'Authorization', 'X-Vision-CSRF',
                             'X-Vision-Project', 'X-Vision-Context'],
            'expose_headers': ['X-Vision-Context']}


def browser_origin_allowed(app, origin, scheme):
    return scheme in {'https','wss'} and origin in getattr(app.state, 'browser_origins', ())


def configure_oidc(app, providers):
    """Server configuration only; providers cannot be enrolled by caller claims."""
    for name, registration in providers.items():
        if not name or len(name) > 80 or not isinstance(registration, OidcRegistration):
            raise ValueError('Invalid OIDC provider registration')
        issuer = urlsplit(registration.issuer)
        callback = urlsplit(registration.redirect_uri)
        if (issuer.scheme != 'https' or not issuer.hostname or issuer.username or issuer.password
                or issuer.query or issuer.fragment or callback.scheme != 'https'
                or callback.username or callback.password or callback.query or callback.fragment
                or not registration.client_id):
            raise ValueError('OIDC issuer and callback require trusted HTTPS configuration')
        if f'https://{callback.netloc}' not in getattr(app.state, 'browser_origins', ()):
            raise ValueError('OIDC callback origin must be configured for browser sessions')
    app.state.oidc_providers = dict(providers)


def permission_action(path, method):
    """Name the existing routing policy without accepting arbitrary actions."""
    import re
    if method in {'GET','HEAD','OPTIONS'}:return 'project.read'
    if re.fullmatch(r'/api/fleet/targets/[a-f0-9]{32}/emergency-rollback',path):
        # The accepted endpoint audits requests then applies owner/admin gating.
        return 'fleet.emergency.request'
    if path=='/api/context/artifacts' or path=='/api/dataset/artifacts/ingest' or re.fullmatch(r'/api/artifacts/uploads(?:/[a-f0-9]{32}(?:/complete)?)?',path):return 'artifact.upload'
    if path.startswith('/api/artifacts/'):return 'artifact.manage'
    if path.startswith('/api/capture-intake/'):
        return 'capture.register' if path.endswith('/register') else 'review.approve'
    if path.startswith('/api/team-data/'):
        return 'label.write' if '/lease/' in path else 'review.approve'
    if path.startswith('/api/product-delivery/'):
        suffix=path.removeprefix('/api/product-delivery/')
        if suffix=='diagnostics' or (suffix.startswith('packages/') and suffix.endswith('/select')):return 'delivery.select'
        if suffix=='operator/inspect':return 'label.write'
        if suffix=='protocol-test' or (suffix.startswith('packages/') and suffix.endswith('/verify')):return 'training.execute'
        if suffix.startswith('servers/') and suffix.endswith('/preflight'):return 'delivery.preflight'
        return 'review.approve'
    if re.fullmatch(r'/api/export/flow/optimization-jobs/[^/]+/approve',path):return 'review.approve'
    if path=='/api/dataset/imports' or path.startswith('/api/dataset/imports/'):
        return 'review.approve' if path.endswith('/accept') else 'label.write'
    if path.startswith(('/api/annotations/','/api/label-candidates/','/api/label-suggestions/','/api/dataset/metadata/','/api/dataset/formats/','/api/data-workbench/')):return 'label.write'
    if path.startswith(('/api/training/','/api/engine/','/api/automated-training/','/api/patch-classification/','/api/rotation/','/api/ocr/','/api/rotated-detection/','/api/enhancement/','/api/defect-gan/','/api/evaluation/','/api/training-workspace/')):return 'training.execute'
    if path.startswith(('/api/flowchart/','/api/inspections/','/api/export/','/api/geometry/','/api/flow-workspace/','/api/flow-evaluations')):return 'flow.execute'
    if path=='/api/image-truth' or path.startswith(('/api/image-truth/','/api/model-deployments/','/api/runtime-services/','/api/model-operations/','/api/fleet/')):return 'review.approve'
    if path.startswith('/api/compute/jobs'):return 'compute.execute'
    return 'project.manage'
