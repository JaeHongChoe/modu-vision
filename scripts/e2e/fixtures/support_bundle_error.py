"""Controlled failed-journal strings exercise privacy; no real inspection claim."""
import hashlib,json,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from PIL import Image
from backend.engine.inspection_service import InspectionStore

root=Path(sys.argv[1]).resolve();project=Path(json.loads(sys.argv[2])['project_dir']).resolve();assert project.is_relative_to(root)
source=root/'support-originals';source.mkdir();image=source/'owned.png';Image.new('RGB',(16,12),'white').save(image);digest=hashlib.sha256(image.read_bytes()).hexdigest()
token='ghp_'+'q'*36;endpoint='https'+':'+chr(47)*2+'diagnostic.example.invalid:bad-port/'+token;address='10.'+'55.8.9';contact='fixture'+'@'+'example.invalid';private=chr(47).join(['','Users','Fixture','private.png'])
error='Controlled fixture failed: '+endpoint+'; token '+token+'; '+address+'; '+contact+'; '+private
store=InspectionStore(project/'runtime_service'/'state')
with sqlite3.connect(store.database) as conn:
 conn.execute('INSERT INTO jobs(job_id,image_path,image_id,image_sha256,source,state,verdict,model_verdict,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',('owned_failed_fixture',str(image),'owned',digest,'fixture','failed',None,None,error,'2026-10-06T00:00:00Z','2026-10-06T00:00:00Z'))
print(json.dumps({'source':str(source),'image':str(image),'image_sha256':digest,'database':str(store.database),'forbidden':[token,address,contact,private,'diagnostic.example.invalid','bad-port'],'controlled_failure_fixture':True,'real_inspection':False}))
