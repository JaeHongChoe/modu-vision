"""Read-only current graph review and owned service observations for a cycle."""
from pathlib import Path
import re
import sqlite3
import httpx
from backend.engine.image_truth import digest


def current_delivery(project,prepared,*,accounts=None):
    result={'whole_flow_current':False,'whole_flow_revision_id':None,
        'service_application_recorded':False,'service_runtime_ready':False,
        'service_deployment_id':None,'device_accepted':False,'reasons':[]}
    try:
        from backend.engine.whole_flow_approval import current_approval
        review=current_approval(project,accounts=accounts)
        if (not review or review.get('validity',{}).get('valid') is not True
                or review['version_id']!=prepared['version_id']
                or review['graph_sha256']!=prepared['graph_sha256']):
            raise ValueError('The exact prepared candidate has no current selected whole-flow review')
        result.update(whole_flow_current=True,whole_flow_revision_id=review['revision_id'])
        root=Path(project['project_dir'])/'runtime_service'
        paths=(root,root/'service.json',root/'runtime_deployments.sqlite3')
        if any(p.is_symlink() for p in (*paths,*root.parents)):
            raise ValueError('Candidate service observation cannot follow links')
        if not all(p.is_file() for p in paths[1:]):
            raise ValueError('No existing candidate service application is recorded')
        # Missing service state is never initialized and nothing is applied or
        # started. Central qualification rechecks bytes and live authority.
        from backend.engine.managed_service import ManagedService
        service=ManagedService(project['project_dir']);active=service.ledger.active()
        if service.ledger.diagnostics()['pending'] is not None:
            raise ValueError('Service has an unresolved application/recovery operation')
        if not active or not re.fullmatch('[0-9a-f]{32}',active.get('deployment_id','')):
            raise ValueError('No current committed candidate service application')
        release=active['release'];bound=release.get('whole_flow_review') or {}
        if (bound.get('revision_id')!=review['revision_id']
                or bound.get('graph_sha256')!=prepared['graph_sha256']):
            raise ValueError('Recorded service belongs to another reviewed graph')
        service.check_live_release(release,project,accounts=accounts)
        ack=active['ack']
        if (ack.get('status')!='ready' or any(ack.get(k)!=release.get(k)
                for k in ('manifest_sha256','device','package_path','release_policy'))):
            raise ValueError('Committed service acknowledgment does not match the qualified release')
        result.update(service_application_recorded=True,service_deployment_id=active['deployment_id'])
        runtime=service.readback()  # Requires the original owned process identity.
        if (runtime.get('status')!='ready' or any(runtime.get(k)!=release.get(k)
                for k in ('manifest_sha256','device','package_path','release_policy'))):
            raise ValueError('Candidate application is recorded; its matching owned service is not currently ready')
        result['service_runtime_ready']=True
    except (ValueError,OSError,KeyError,TypeError,sqlite3.Error,httpx.HTTPError) as exc:
        result['reasons']=[str(exc)]
    result['snapshot_sha256']=digest(result)
    return result
