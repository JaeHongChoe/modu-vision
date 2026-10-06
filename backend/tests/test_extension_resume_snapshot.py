"""Resume lineage must identify the payload restored, even if latest advances."""
import hashlib

import torch

from backend.engine.training_resume import read_training_state, resume_lineage


def test_resume_lineage_remains_bound_to_the_loaded_state(tmp_path):
    path = tmp_path / 'latest_training_state.pt'
    state = {
        'schema_version': 1, 'semantics': 'exact_resume', 'boundary': 'epoch',
        'identity': {'recipe': {}}, 'model_state_dict': {}, 'optimizer_state_dict': {},
        'scheduler_state_dict': {}, 'scaler_state_dict': {}, 'scaler_enabled': False,
        'rng_state': {}, 'early_stopping': {}, 'next_epoch': 2, 'global_step': 8,
    }
    torch.save(state, path)
    loaded_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    loaded = read_training_state(path)
    torch.save({**state, 'next_epoch': 3, 'global_step': 12}, path)
    lineage = resume_lineage(path, loaded)
    assert lineage['checkpoint_sha256'] == loaded_hash
    assert lineage['next_epoch'] == 2 and lineage['global_step'] == 8
