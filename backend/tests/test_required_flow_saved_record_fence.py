"""Root-only real GET/POST coupling: saved-file bytes remain the same witness.

The original selected version id and graph remain unchanged; only the original
saved-record metadata changes. It must not pass the POST-selected byte fence.
"""
import json
from pathlib import Path
from backend.api import routes_flowchart
from backend.tests import test_required_flow_http_readback as original

def test_same_version_same_graph_changed_saved_record_is_not_promoted(tmp_path,monkeypatch):
    app,selector,session,project,output,_=original.ready(tmp_path,monkeypatch)
    c=original.client(app,session)
    try:
        original.post(c,project)
        positive=c.get('/api/project/current');assert positive.status_code==200,positive.text
        initial=original.rows(output);assert len(initial)==1 and initial[0]['complete']is True
        assert initial[0]['path']=='/api/project/current'
        before={p.name:p.read_bytes()for p in output.glob('readback-???.json')}
        path=Path(project['project_dir'])/'flowcharts/versions'/f'{original.SAVED}.json'
        record=json.loads(path.read_bytes());unchanged_pipeline=record['pipeline'];record['saved_at_ns']=2
        routes_flowchart._write_json(path,record)
        response=c.get('/api/flowchart/pipelines/'+original.SAVED);assert response.status_code==200,response.text
        assert json.loads(response.content)==unchanged_pipeline
    finally:c.close()
    assert before=={p.name:p.read_bytes()for p in output.glob('readback-???.json')}
