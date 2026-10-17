"""Device discovery is a fixed child command, never a second owned app."""
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "scripts/frozen_backend_entry.py"


@pytest.mark.parametrize("frozen,external", [(False, False), (True, False), (True, True)])
def test_discovery_uses_fixed_frozen_command_or_existing_external_python(monkeypatch, frozen, external):
    from backend.engine import openvino_runtime, runtime_deadline
    monkeypatch.delitem(sys.modules, "openvino", raising=False)
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.delenv("VISION_OPENVINO_PYTHON", raising=False)
    if external:
        monkeypatch.setenv("VISION_OPENVINO_PYTHON", "/owned/external/python")
    calls = []
    expected = {"backend": "openvino", "version": "fixture", "devices": ["CPU"],
                "names": {"CPU": "Owned fixture CPU"}}
    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return {"status": "completed", "returncode": 0, "stdout": json.dumps(expected)}
    monkeypatch.setattr(runtime_deadline, "execute_owned_process", execute)
    assert openvino_runtime.available_openvino_devices() == expected
    argv, options = calls[0]
    assert len(calls) == 1 and options == {"deadline_ms": 15000}
    assert argv[0] == ("/owned/external/python" if external else sys.executable)
    if frozen and not external:
        assert argv == [sys.executable, "--openvino-device-discovery"]
    else:
        assert argv[1] == "-c" and len(argv) == 3
        assert "c.available_devices" in argv[2]


def test_fixed_discovery_dispatch_does_not_consume_app_handshake(monkeypatch, capsys):
    from backend.engine import application_launch_handshake
    class Core:
        available_devices = ["CPU"]
        def get_property(self, device, name):
            assert (device, name) == ("CPU", "FULL_DEVICE_NAME")
            return "Owned fixture CPU"
    monkeypatch.setitem(sys.modules, "openvino", SimpleNamespace(__version__="fixture", Core=Core))
    monkeypatch.setitem(sys.modules, "pyarrow", SimpleNamespace(owned_fixture=True))
    monkeypatch.setenv("VISION_APPLICATION_LAUNCH_FD", "2147483647")
    monkeypatch.setattr(application_launch_handshake, "early_backend_bootstrap",
                        lambda: pytest.fail("Discovery child entered app handshake"))
    monkeypatch.setattr(sys, "argv", ["owned-frozen", "--openvino-device-discovery"])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(ENTRY), run_name="__main__")
    assert result.value.code == 0
    assert json.loads(capsys.readouterr().out) == {
        "backend": "openvino", "version": "fixture", "devices": ["CPU"],
        "names": {"CPU": "Owned fixture CPU"}}
    assert sys.modules["pyarrow"] is None


def test_fixed_discovery_rejects_arguments_before_any_app_or_openvino_import(monkeypatch):
    from backend.engine import application_launch_handshake
    monkeypatch.setattr(application_launch_handshake, "early_backend_bootstrap",
                        lambda: pytest.fail("Invalid discovery entered app handshake"))
    monkeypatch.setattr(sys, "argv", ["owned-frozen", "--openvino-device-discovery", "untrusted-code"])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(ENTRY), run_name="__main__")
    assert result.value.code == 2
