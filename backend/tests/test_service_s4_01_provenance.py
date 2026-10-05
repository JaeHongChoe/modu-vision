import hashlib
from backend.engine import training_workspace as workspace

def test_readiness_shows_exact_file_hash_without_declaring_content_or_quality_verified(tmp_path,monkeypatch):
    file=tmp_path/'model.safetensors';file.write_bytes(b'owned synthetic header only')
    monkeypatch.setattr(workspace,'_package_available',lambda _:True)
    result=workspace.model_readiness('classification','dinov3_vits16','cpu',str(file))
    assert result['weights']['sha256']==hashlib.sha256(file.read_bytes()).hexdigest()
    assert result['weights']['origin']=='explicit_file'
    assert result['weights']['content_verified'] is False and result['execution_verified'] is False and result['quality_approved'] is False

def test_unreadable_weight_hash_refuses_readiness_instead_of_offering_submission(tmp_path,monkeypatch):
    from backend.engine import model_backbones
    file=tmp_path/'model.safetensors';file.write_bytes(b'owned')
    def refused(_):raise PermissionError('owned read refused')
    monkeypatch.setattr(model_backbones,'checkpoint_sha256',refused)
    result=workspace.model_readiness('classification','dinov3_vits16','cpu',str(file))
    assert result['ready'] is False and result['weights']['state']=='unreadable'
    assert result['weights']['sha256'] is None and 'import_official_weights' in result['next_actions']
