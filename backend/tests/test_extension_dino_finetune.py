"""Real offline DINO encoder gradients and portable legacy checkpoints."""
import pytest
import torch


@pytest.mark.parametrize('mode', ['head_only', 'partial', 'full'])
def test_selected_dino_training_scope_updates_only_requested_weights(mode):
    from backend.engine.model_backbones import DinoTaskModel
    torch.set_num_threads(1)
    torch.manual_seed(12)
    model = DinoTaskModel('classification', 'dinov3_vits16', 2, pretrained=False,
                          train_mode=mode, partial_blocks=1)
    model.train()
    first = model.encoder.blocks[0].attn.qkv.weight
    last = model.encoder.blocks[-1].attn.qkv.weight
    first_before, last_before, head_before = first.detach().clone(), last.detach().clone(), model.head.weight.detach().clone()
    optimizer = torch.optim.SGD([p for p in model.parameters() if p.requires_grad], lr=.01)
    loss = torch.nn.functional.cross_entropy(model(torch.rand(2, 3, 32, 32)), torch.tensor([0, 1]))
    loss.backward(); optimizer.step()
    assert not torch.equal(head_before, model.head.weight)
    assert (first.grad is not None) == (mode == 'full')
    assert (last.grad is not None) == (mode != 'head_only')
    assert torch.equal(first_before, first) == (mode != 'full')
    assert torch.equal(last_before, last) == (mode == 'head_only')
    assert model.model_metadata['train_mode'] == mode
    assert model.model_metadata['encoder_frozen'] == (mode == 'head_only')
    assert model.encoder.blocks[0].training == (mode == 'full')
    assert model.encoder.blocks[-1].training == (mode != 'head_only')


def test_default_dino_checkpoint_reconstructs_offline_after_finetune(tmp_path):
    from backend.engine.classification import create_classification_model
    from backend.engine.exporter import load_checkpoint_and_reconstruct_model
    torch.set_num_threads(1)
    model = create_classification_model('dinov3_vits16', 2, pretrained=False,
                                        train_mode='partial', partial_blocks=1).eval()
    pixels = torch.rand(1, 3, 32, 32)
    expected = model(pixels)
    path = tmp_path / 'best_model.pt'
    torch.save({'task': 'classification', 'classes': ['good', 'defect'], 'image_size': [32, 32],
                'model_state_dict': model.state_dict(), **model.model_metadata}, path)
    rebuilt, metadata, _ = load_checkpoint_and_reconstruct_model(path)
    torch.testing.assert_close(rebuilt.eval()(pixels), expected)
    assert metadata['train_mode'] == 'partial'
    legacy = create_classification_model('dinov3_vits16', 2, pretrained=False)
    assert all(not p.requires_grad for p in legacy.encoder.parameters())


def test_invalid_finetune_scope_is_rejected_before_model_creation():
    from backend.engine.model_backbones import DinoTaskModel
    with pytest.raises(ValueError, match='train_mode'):
        DinoTaskModel('classification', 'dinov3_vits16', 2, pretrained=False, train_mode='automatic')
    with pytest.raises(ValueError, match='partial_blocks'):
        DinoTaskModel('classification', 'dinov3_vits16', 2, pretrained=False, train_mode='partial', partial_blocks=0)
