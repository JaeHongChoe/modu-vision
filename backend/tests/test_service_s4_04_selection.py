"""Detection checkpoint selection identifies validation loss, not heldout mAP."""
import json
import torch
from backend.engine.trainer import UnifiedAutoMLTrainer


def test_detection_checkpoint_preserves_explicit_minimum_validation_loss_selection(tmp_path):
    trainer = UnifiedAutoMLTrainer(task='detection', dataset_path=tmp_path,
        output_dir=tmp_path / 'checkpoint', device='cpu')
    trainer._save_checkpoint(epoch=3, model=torch.nn.Linear(2, 2), metric=.123456,
        classes=['scratch', 'stain'], img_size=(64, 64), elapsed=1)
    metadata = json.loads((trainer.output_dir / 'model_meta.json').read_text())
    checkpoint = torch.load(trainer.output_dir / 'best_model.pt', weights_only=False)
    expected = {'metric': 'val_loss', 'direction': 'min', 'split': 'val', 'epoch': 3}
    assert metadata['checkpoint_selection'] == expected
    assert checkpoint['checkpoint_selection'] == expected
    assert metadata['best_metric'] == .12346
    assert checkpoint['best_metric'] == .123456
