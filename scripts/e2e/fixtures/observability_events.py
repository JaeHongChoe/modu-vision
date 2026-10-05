"""Controlled durable lifecycle/epoch/error events in an owned E2E workspace.

These callbacks are synthetic log inputs, not representative model training.
The companion UI case separately executes the actual owned CPU inspector.
"""
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from backend.contracts.context import ContextRegistry,ProjectContext,current_project_context
from backend.engine.job_store import ledger
from backend.api.websocket_telemetry import WebSocketTelemetryCallback

root=Path(sys.argv[1]).resolve();run=Path(os.environ.get('MV_E2E_RUN_DIR','missing')).resolve()
if root.parent!=run/'ws' or not re.fullmatch(r'\d+-[a-f0-9]{8}-r\d+',root.name):raise ValueError('Owned E2E workspace required')
if Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR']).resolve()!=root/'userData':raise ValueError('Owned application profile required')
context=ProjectContext.model_validate_json(sys.argv[2])
# Chrome and Electron have distinct owned registry roots. Select only the
# existing registry containing the exact workspace/project from the API.
matching=[]
for directory in (root/'projects',root/'userData'/'projects'):
 database=directory/'.context.sqlite3'
 if not database.is_file() or database.is_symlink():continue
 with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
  if db.execute('SELECT 1 FROM project_locations WHERE workspace_id=? AND project_id=?',(context.workspace_id,context.project_id)).fetchone():matching.append(directory)
if len(matching)!=1:raise ValueError('Exactly one originating E2E project registry required')
registry=ContextRegistry(matching[0]);key=registry.project_key(context)
jobs=ledger();job=jobs.submit(context,key,'training',{'fixture':'controlled-observability-epochs','mode':sys.argv[3]})
jobs.transition(job.id,job.revision,'start');token=current_project_context.set(context)
try:
 callback=WebSocketTelemetryCallback(job.id)
 callback.on_training_start({'epochs':60,'token':'private-e2e-log-fixture'})
 if sys.argv[3]=='seed':
  for epoch in range(60):callback.on_epoch_end(epoch,60,.5,.6,.01,{})
  callback.on_training_completed(job.id,1,.6,'/private/fixture/checkpoint.pt');jobs.transition(job.id,jobs.get(job.id).revision,'complete')
 else:
  callback.on_error(ValueError('Controlled post-opt-in failure token=private-e2e-log-fixture'),'controlled_fixture')
  jobs.transition(job.id,jobs.get(job.id).revision,'fail',{'reason':'Controlled post-opt-in failure'})
finally:current_project_context.reset(token)
print(json.dumps({'job_id':job.id,'controlled_callback_events':True,'actual_training':False,'context':context.model_dump()}))
