import json
from pathlib import Path
import pytest
from backend.engine.edge_runtime import create_edge_profile
from backend.tests.test_runtime_deadline_sdk import real_package


def test_jetson_cuda_profile_requires_linux_and_vendor_runtime():
    profile=create_edge_profile('linux','aarch64','torch>=2.4\n',device='cuda:0')
    assert profile['profile']=='edge_cuda' and profile['device']=='cuda:0'
    assert profile['target']=={'os':'linux','architecture':'arm64'}
    assert profile['torch_provisioning']=='vendor_preinstalled'
    with pytest.raises(ValueError,match='Linux'):
        create_edge_profile('macos','arm64','torch>=2.4\n',device='cuda')


def test_cpu_profile_and_package_configuration_are_verified_on_reopen(real_package):
    from backend.engine.flow_package_runtime import verify_flow_package,Predictor
    package,image=real_package
    manifest=json.loads((package/'manifest.json').read_text())
    assert manifest['runtime']=={'device':'cpu','cpu_threads':1,'deadline_ms':None}
    assert Predictor(package).options['deadline_ms']==300000
    (package/'runtime_config.json').write_text(json.dumps({'device':'cuda','deadline_ms':1,'cpu_threads':1}))
    with pytest.raises(ValueError,match='checksum|configuration'):verify_flow_package(package)
