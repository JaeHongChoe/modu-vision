"""
E2E Hardware Device and Acceleration Stress Test Suite.
Validates end-to-end tensor workloads, memory cleanup, and CPU fallback equivalence.
"""

import pytest
import torch
import torch.nn as nn


def test_device_batch_tensor_math(device_module):
    """E2E Test: Multi-layer vision backbone simulation on detected device."""
    dev = device_module.get_device()

    class VisionBlock(nn.Module):
        def __init__(self):
            super().__init__()
            self.conv1 = nn.Conv2d(3, 16, kernel_size=3, padding=1)
            self.bn1 = nn.BatchNorm2d(16)
            self.relu = nn.ReLU()
            self.conv2 = nn.Conv2d(16, 32, kernel_size=3, padding=1)
            self.pool = nn.AdaptiveAvgPool2d((1, 1))
            self.fc = nn.Linear(32, 4)

        def forward(self, x):
            x = self.relu(self.bn1(self.conv1(x)))
            x = self.relu(self.conv2(x))
            x = self.pool(x)
            x = torch.flatten(x, 1)
            return self.fc(x)

    model = VisionBlock()
    model = device_module.to_device(model, dev)

    batch_x = torch.randn(8, 3, 64, 64, device=dev)
    batch_y = torch.randint(0, 4, (8,), device=dev)

    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.CrossEntropyLoss()

    # Run 3 training steps
    for _ in range(3):
        optimizer.zero_grad()
        logits = model(batch_x)
        loss = criterion(logits, batch_y)
        loss.backward()
        optimizer.step()

        assert not torch.isnan(loss)
        assert loss.item() > 0.0


def test_device_memory_pressure_and_cleanup(device_module):
    """E2E Test: Allocates temporary tensors, validates telemetry, and cleans cache."""
    dev = device_module.get_device()

    before_stats = device_module.get_memory_stats(dev)
    assert before_stats.total_ram_gb > 0.0

    # Allocate a batch of tensors (~20MB)
    tensors = [torch.randn(100, 100, 50, device=dev) for _ in range(10)]
    mid_stats = device_module.get_memory_stats(dev)
    assert mid_stats.ram_percent > 0.0

    del tensors
    device_module.clear_device_cache(dev)

    after_stats = device_module.get_memory_stats(dev)
    assert after_stats.free_ram_gb > 0.0


def test_device_fallback_transparent_equivalence(device_module):
    """E2E Test: Validates that computation results on device match CPU reference."""
    dev = device_module.get_device()
    cpu_dev = torch.device("cpu")

    torch.manual_seed(42)
    linear_cpu = nn.Linear(16, 4)
    linear_dev = nn.Linear(16, 4)
    linear_dev.load_state_dict(linear_cpu.state_dict())
    linear_dev = device_module.to_device(linear_dev, dev)

    input_cpu = torch.randn(4, 16)
    input_dev = device_module.to_device(input_cpu.clone(), dev)

    out_cpu = linear_cpu(input_cpu)
    out_dev = linear_dev(input_dev)

    assert torch.allclose(out_cpu, out_dev.cpu(), atol=1e-4, rtol=1e-4)
