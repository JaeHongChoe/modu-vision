"""
Milestone M1 Adversarial Stress Test Suite (Challenger 1).
Validates:
1. Deep nested nn.Module (submodule within submodule) registering float64 buffers and parameters,
   transferred to Apple Silicon mps:0 via to_device(m, 'mps'), followed by live forward/backward pass.
2. generate_synthetic_dataset() with individual task flags:
   - task="classification" -> only classification directory generated
   - task="detection" -> only detection directory generated
   - task="segmentation" -> only segmentation directory generated
   - task="anomaly" -> only anomaly directory generated
   - task="invalid" -> raises ValueError
"""

import os
import platform
import pytest
import torch
import torch.nn as nn


class DeepLeafSubmodule(nn.Module):
    """Innermost submodule registering float64 parameters and buffers."""

    def __init__(self, in_features: int = 8, out_features: int = 8):
        super().__init__()
        self.weight = nn.Parameter(torch.randn(in_features, out_features, dtype=torch.float64))
        self.bias = nn.Parameter(torch.randn(out_features, dtype=torch.float64))
        # Persistent buffer
        self.register_buffer("running_scale", torch.tensor([1.5] * out_features, dtype=torch.float64))
        # Non-persistent buffer
        self.register_buffer("activation_mask", torch.ones(out_features, dtype=torch.float64), persistent=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = x @ self.weight + self.bias
        return h * self.running_scale * self.activation_mask


class DeepIntermediateSubmodule(nn.Module):
    """Intermediate submodule containing leaf submodule plus its own float64 parameters/buffers."""

    def __init__(self, in_features: int = 8, out_features: int = 8):
        super().__init__()
        self.leaf = DeepLeafSubmodule(in_features, out_features)
        self.mid_weight = nn.Parameter(torch.randn(out_features, out_features, dtype=torch.float64))
        self.register_buffer("mid_bias", torch.tensor([0.25] * out_features, dtype=torch.float64))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.leaf(x)
        return (h @ self.mid_weight) + self.mid_bias


class DeepRootModule(nn.Module):
    """Root module containing intermediate submodule and root-level float64 parameters/buffers."""

    def __init__(self, in_features: int = 8, out_classes: int = 2):
        super().__init__()
        self.intermediate = DeepIntermediateSubmodule(in_features, 8)
        self.classifier = nn.Parameter(torch.randn(8, out_classes, dtype=torch.float64))
        self.register_buffer("class_prior", torch.zeros(out_classes, dtype=torch.float64))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        features = self.intermediate(x)
        logits = (features @ self.classifier) + self.class_prior
        return logits


def test_adversarial_deep_nested_module_mps(device_module):
    """
    Adversarial Challenge 1:
    Deep nested nn.Module (root -> intermediate -> leaf) registering float64 buffers and parameters.
    Transferred via to_device(m, 'mps'), followed by live forward pass, backward pass,
    and optimizer step on Apple Silicon MPS.
    """
    mps_available = hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
    target_device_str = "mps:0" if mps_available else "cpu"
    target_device = torch.device(target_device_str)

    model = DeepRootModule(in_features=8, out_classes=2)

    # 1. Assert pre-transfer state: all parameters and buffers are float64
    for name, param in model.named_parameters():
        assert param.dtype == torch.float64, f"Parameter {name} must start as float64"
    for name, buf in model.named_buffers():
        assert buf.dtype == torch.float64, f"Buffer {name} must start as float64"

    # 2. Transfer using to_device with string 'mps' / 'mps:0'
    model = device_module.to_device(model, target_device_str)

    # 3. Assert post-transfer state: all converted to float32 on target device
    for name, param in model.named_parameters():
        assert param.device.type == target_device.type, f"Parameter {name} device mismatch"
        if target_device.type == "mps":
            assert param.dtype == torch.float32, f"Parameter {name} not downcasted to float32 on MPS"

    for name, buf in model.named_buffers():
        assert buf.device.type == target_device.type, f"Buffer {name} device mismatch"
        if target_device.type == "mps":
            assert buf.dtype == torch.float32, f"Buffer {name} not downcasted to float32 on MPS"

    # 4. Live forward pass
    batch_size = 4
    x = torch.randn(batch_size, 8, dtype=torch.float32, device=target_device, requires_grad=True)
    out = model(x)
    assert out.shape == (batch_size, 2)
    assert out.device.type == target_device.type
    assert not torch.isnan(out).any()

    # 5. Live backward pass
    target = torch.tensor([0, 1, 0, 1], device=target_device, dtype=torch.int64)
    criterion = nn.CrossEntropyLoss()
    loss = criterion(out, target)
    assert not torch.isnan(loss)
    loss.backward()

    # 6. Verify gradient propagation to all nested parameters
    for name, param in model.named_parameters():
        assert param.grad is not None, f"Parameter {name} has no gradient"
        assert param.grad.device.type == target_device.type
        assert not torch.isnan(param.grad).any()

    # 7. Optimizer step
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    optimizer.step()
    optimizer.zero_grad()


@pytest.mark.parametrize("task_name", ["classification", "detection", "segmentation", "anomaly"])
def test_adversarial_synthetic_generator_individual_task_flags(synthetic_module, loader_module, temp_dir, task_name):
    """
    Adversarial Challenge 2:
    generate_synthetic_dataset() with individual task flags generates ONLY the requested task directory.
    """
    task_dir = os.path.join(temp_dir, f"test_{task_name}")
    os.makedirs(task_dir, exist_ok=True)

    summary = synthetic_module.generate_synthetic_dataset(
        output_dir=task_dir,
        num_samples=10,
        task=task_name,
        seed=42,
    )

    assert summary["status"] == "success"
    assert summary["task"] == task_name
    assert summary["total_images"] == 10

    top_level_entries = set(os.listdir(task_dir))
    subdirs = {e for e in top_level_entries if os.path.isdir(os.path.join(task_dir, e))}

    # Strictly only the target task directory should exist
    assert subdirs == {task_name}, f"For task={task_name}, expected subdirs to be {{{task_name}}}, got {subdirs}"

    # Verify other task directories do NOT exist
    all_known_tasks = {"classification", "detection", "segmentation", "anomaly"}
    other_tasks = all_known_tasks - {task_name}
    for other in other_tasks:
        assert not os.path.exists(os.path.join(task_dir, other)), f"Unrequested task directory {other} must not exist"

    # Verify loader compatibility on the isolated generated task directory
    task_subdir = os.path.join(task_dir, task_name)
    inspection = loader_module.inspect_dataset(task_subdir, task=task_name)
    assert inspection.total_images == 10


def test_adversarial_synthetic_generator_invalid_task_raises(synthetic_module, temp_dir):
    """
    Adversarial Challenge 2:
    generate_synthetic_dataset() with invalid task names raises ValueError.
    """
    invalid_cases = ["invalid", "unknown_task", "", "robotics", 12345, None]

    for inv in invalid_cases:
        with pytest.raises(ValueError) as exc_info:
            synthetic_module.generate_synthetic_dataset(
                output_dir=os.path.join(temp_dir, "inv"),
                num_samples=10,
                task=inv,
            )
        assert "Invalid task" in str(exc_info.value)
