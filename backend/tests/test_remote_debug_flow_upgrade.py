"""Remote debug uses the real CPU worker and only its ancestor model references."""
import json
from pathlib import Path
from PIL import Image
from backend.engine.flowchart_engine import get_fixed_roi_flowchart
from backend.remote.operations import run_verified_flowchart_on_compute
from backend.remote.worker import run_flowchart
from backend.tests.test_remote_portable_flow import portable_models
from backend.tests.test_remote_operations import FakeAdditionalRemote


def test_selected_server_can_debug_roi_before_a_downstream_model_is_available(tmp_path,monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userdata'))
    project,_,_,image,profile=portable_models(tmp_path)
    Image.new('RGB',(64,64),'red').save(image)
    graph=get_fixed_roi_flowchart(inspection_task='segmentation')
    roi=next(n for n in graph.nodes if n.data.node_type=='fixed_roi')
    roi.data.params['roi_bbox']=[8,8,40,40]
    class RealWorkerRemote(FakeAdditionalRemote):
        def launch(self,profile,argv,run_id):
            self.launches+=1
            status=run_flowchart(self.root/'runs'/run_id/'spec.json')
            assert status['status']=='completed',status
            return 'real-cpu-worker'
    remote=RealWorkerRemote(Path(profile.remote_root))
    result=run_verified_flowchart_on_compute(profile,project,graph.model_dump(),{},image,device='cpu',transport=remote,stop_node_id=roi.id)
    assert result['status']=='partial'
    assert result['stop_node_id']==roi.id
    assert result['execution_target']=='selected_compute'
    assert result['compute_profile_id']==profile.id
    assert next(s for s in result['execution_steps'] if s['node_id']==roi.id)['artifacts'][0]['bbox']==[8,8,40,40]
    spec=json.loads(next((Path(profile.remote_root)/'runs').glob('op_*/spec.json')).read_text())
    assert spec['models']==[]
    assert spec['stop_node_id']==roi.id
