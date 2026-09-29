"""Share the backend's real-module fixtures with integration tests."""

from backend.conftest import device_module, loader_module, synthetic_module, temp_dir

__all__ = ["device_module", "loader_module", "synthetic_module", "temp_dir"]
