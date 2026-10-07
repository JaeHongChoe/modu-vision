import json
import pytest
from backend.tests.test_terminal_remote_history import remote_history,digest

@pytest.mark.parametrize('state',['failed','aborted'])
@pytest.mark.parametrize('damage',['model_bytes','metadata_bytes','missing_manifest','linked_model','relocation_without_originals'])
def test_failed_or_aborted_received_model_bytes_are_bound_before_migration(tmp_path,state,damage):
 from backend.engine.historical_job_binding import preview
 root,scopes,ledger,registry,key,source,output,journal=remote_history(tmp_path,state)
 if damage=='model_bytes':(output/'best_model.pt').write_bytes(b'changed failed model')
 elif damage=='metadata_bytes':(output/'model_meta.json').write_text('{"task":"detection"}')
 elif damage=='missing_manifest':(output/'remote_artifacts.json').unlink()
 elif damage=='linked_model':
  model=output/'best_model.pt';kept=output/'retained.bin';model.rename(kept);model.symlink_to(kept)
 else:
  file=output/'remote_artifacts.json';manifest=json.loads(file.read_text());manifest['relocation']={'remote_dataset_root':'/other','local_dataset_root':str(output)};file.write_text(json.dumps(manifest))
 before=ledger.record('job_legacy');result=preview(root)
 assert result['can_apply'] is False and result['blockers']
 assert ledger.record('job_legacy')==before

@pytest.mark.parametrize('state',['failed','aborted'])
def test_failed_training_without_any_received_model_pair_stays_archival(tmp_path,state):
 from backend.engine.historical_job_binding import preview
 root,scopes,ledger,registry,key,source,output,journal=remote_history(tmp_path,state)
 for name in ('remote_artifacts.json','best_model.pt','model_meta.json'):(output/name).unlink()
 receipt=output/'job_receipt.json';value=json.loads(receipt.read_text());value.pop('checkpoint_sha256');receipt.write_text(json.dumps(value))
 assert preview(root)['can_apply'] is True

@pytest.mark.parametrize('state',['failed','aborted'])
def test_failed_received_pair_without_completed_checkpoint_receipt_stays_hash_bound(tmp_path,state):
 from backend.engine.historical_job_binding import preview
 root,scopes,ledger,registry,key,source,output,journal=remote_history(tmp_path,state)
 receipt=output/'job_receipt.json';value=json.loads(receipt.read_text());value.pop('checkpoint_sha256');receipt.write_text(json.dumps(value))
 assert preview(root)['can_apply'] is True
 (output/'best_model.pt').write_bytes(b'Changed retained partial epoch model')
 assert preview(root)['can_apply'] is False
