"""Central registry of authenticated field agents and acknowledged releases."""
from __future__ import annotations
import io
import json
from pathlib import Path
import sqlite3
import uuid
from urllib.parse import urlsplit,urlunsplit
import zipfile
import httpx
from backend.engine.runtime_deployment import DeploymentLedger


def validate_target_url(value):
    parts=urlsplit(value.strip())
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
        raise ValueError('Agent URL must be an HTTPS origin or loopback SSH tunnel')
    if parts.scheme=='http' and parts.hostname not in ('127.0.0.1','localhost','::1'):raise ValueError('Remote agents require HTTPS; use a loopback SSH tunnel for HTTP')
    return urlunsplit((parts.scheme,parts.netloc,'','',''))


class FleetRegistry:
    def __init__(self,project_dir):
        base=Path(project_dir)
        if base.is_symlink():raise ValueError('Fleet project storage is linked')
        self.root=base/'fleet';self.root.mkdir(parents=True,exist_ok=True)
        if self.root.is_symlink():raise ValueError('Fleet storage is linked')
        self.path=self.root/'agents.sqlite3'
        if self.path.is_symlink():raise ValueError('Fleet database is linked')
        with self.connect() as conn:conn.execute('CREATE TABLE IF NOT EXISTS targets(target_id TEXT PRIMARY KEY,name TEXT NOT NULL,url TEXT NOT NULL,token TEXT NOT NULL)')
        self.path.chmod(0o600)
    def connect(self):
        conn=sqlite3.connect(self.path,timeout=30);conn.row_factory=sqlite3.Row;return conn
    def targets(self):
        with self.connect() as conn:rows=conn.execute('SELECT * FROM targets ORDER BY name').fetchall()
        return [{key:row[key] for key in ('target_id','name','url')}|{'token_set':bool(row['token'])} for row in rows]
    def secret(self,identifier):
        with self.connect() as conn:row=conn.execute('SELECT token FROM targets WHERE target_id=?',(identifier,)).fetchone()
        if not row:raise KeyError(identifier)
        return row[0]
    def target(self,identifier):
        row=next((r for r in self.targets() if r['target_id']==identifier),None)
        if not row:raise KeyError(identifier)
        return row
    def save_target(self,*,name,url,token,target_id=None):
        if not name.strip() or len(name)>100 or not token or len(token)>4096:raise ValueError('Agent name and access token are required')
        url=validate_target_url(url);identifier=target_id or uuid.uuid4().hex
        if len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):raise ValueError('Invalid agent ID')
        with self.connect() as conn:conn.execute('INSERT INTO targets VALUES(?,?,?,?) ON CONFLICT(target_id) DO UPDATE SET name=excluded.name,url=excluded.url,token=excluded.token',(identifier,name.strip(),url,token))
        return self.target(identifier)
    def ledger(self,identifier):
        self.target(identifier);return DeploymentLedger(self.root/identifier)
    def client(self,identifier):
        return httpx.Client(base_url=self.target(identifier)['url'],headers={'Authorization':'Bearer '+self.secret(identifier)},timeout=30,follow_redirects=False)
    def readback(self,identifier):
        try:
            with self.client(identifier) as client:response=client.get('/agent/v1/runtime');response.raise_for_status();runtime=response.json()
            active=self.ledger(identifier).active();expected=(active or {}).get('release',{}).get('manifest_sha256')
            return {'target':self.target(identifier),'runtime':runtime,'active':active,'matches_active':bool(expected and runtime.get('status')=='ready' and runtime.get('manifest_sha256')==expected and runtime.get('device')==(active or {}).get('release',{}).get('device'))}
        except (httpx.HTTPError,ValueError) as exc:return {'target':self.target(identifier),'runtime':{'status':'disconnected'},'error':type(exc).__name__,'active':self.ledger(identifier).active(),'matches_active':False}
    def apply(self,identifier,release,*,reviewer,restored_from=None):
        def apply_remote(selected):
            with self.client(identifier) as client:
                response=client.post('/agent/v1/releases',content=package_archive(Path(selected['package_path'])),headers={'Content-Type':'application/zip','X-Manifest-SHA256':selected['manifest_sha256']});response.raise_for_status()
                response=client.post('/agent/v1/apply',json={'manifest_sha256':selected['manifest_sha256'],'device':selected['device']});response.raise_for_status()
                ack=client.get('/agent/v1/runtime');ack.raise_for_status();return ack.json()
        return self.ledger(identifier).apply(release,apply_remote,reviewer=reviewer,restored_from=restored_from)
    def rollback(self,identifier,deployment_id,*,reviewer):
        target=next((r for r in self.ledger(identifier).history() if r['deployment_id']==deployment_id),None)
        if not target:raise KeyError(deployment_id)
        selected=self.apply(identifier,target['release'],reviewer=reviewer,restored_from=deployment_id)
        return selected


def package_archive(package):
    from backend.engine.flow_package_runtime import verify_flow_package
    verify_flow_package(package);manifest=json.loads((package/'manifest.json').read_text());stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in ['manifest.json',*[row['path'] for row in manifest['files']]]:
            path=package/relative
            if path.is_symlink() or not path.resolve().is_relative_to(package.resolve()):raise ValueError('Release archive contains linked or escaping files')
            archive.write(path,relative)
    return stream.getvalue()
