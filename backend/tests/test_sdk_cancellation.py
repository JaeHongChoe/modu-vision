"""SDK cancellation terminates owned work and leaves a reusable handle."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest

from backend.tests.test_runtime_deadline_sdk import real_package
from backend.tests.test_gan_source_composition import generator


def cancel_started_call(executor, invoke, monkeypatch):
    # Observe the actual owned child launch, rather than guessing when inference starts.
    started = threading.Event()
    original = subprocess.Popen

    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        started.set()
        return child

    monkeypatch.setattr(subprocess, 'Popen', launch)
    results, errors = [], []

    def run():
        try:
            results.append(invoke())
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert started.wait(10), errors
        assert executor.cancel() is True
    finally:
        thread.join(15)
    assert not thread.is_alive(), 'Cancellation must not wait for the inference deadline'
    assert not errors, errors
    result = results[0]
    assert result['status'] == 'cancelled'
    assert result['final_verdict'] == 'REVIEW'
    assert result['rejection_reason'] == 'CANCELLED'
    assert result['deadline']['terminated'] is True
    assert executor.cancel() is False
    if os.name != 'nt':
        with pytest.raises(ProcessLookupError):
            os.kill(result['deadline']['pid'], 0)
    return result


def test_python_cancellation_reaps_child_and_reuses_handle(real_package, monkeypatch):
    from backend.engine.flow_package_runtime import Executor
    package, image = real_package
    executor = Executor(package, deadline_ms=30000, cpu_threads=1)
    assert executor.cancel() is False
    cancel_started_call(executor, lambda: executor.predict(image, 'cancelled-image'), monkeypatch)
    result = executor.execute({'image_path': str(image), 'image_id': 'next-image'})
    assert result['final_verdict'] == 'NG' and result['image_id'] == 'next-image'
    assert executor.cancel() is False
    with pytest.raises(RuntimeError, match='Inspection image not found'):
        executor.predict(image.with_name('absent.png'))
    assert executor.cancel() is False, 'Exceptions must also clear the active cancellation scope'


def test_generator_cancellation_publishes_no_candidate_and_reuses_handle(generator, tmp_path, monkeypatch):
    from backend.engine.gan_package_runtime import build_generator_package, GeneratorExecutor
    package = build_generator_package(generator, tmp_path / 'generator-package')
    executor = GeneratorExecutor(package, deadline_ms=30000)
    output = tmp_path / 'cancelled-output'
    cancel_started_call(executor, lambda: executor.execute({'output_dir': str(output), 'count': 1}), monkeypatch)
    assert not output.exists()
    assert not list(tmp_path.glob('.native-generation-*'))
    following = executor.execute({'output_dir': str(tmp_path / 'next-output'), 'count': 1, 'seed': 7})
    assert Path(following['candidates'][0]['path']).is_file()
    assert following['candidates'][0]['status'] == 'synthetic_unreviewed'


def _assert_demo(process):
    assert process.returncode == 0, process.stdout + process.stderr
    payload = json.loads(process.stdout)
    assert payload['cancelled']['status'] == 'cancelled'
    assert payload['cancelled']['rejection_reason'] == 'CANCELLED'
    assert payload['cancelled']['final_verdict'] == 'REVIEW'
    assert payload['cancelled']['deadline']['terminated'] is True
    assert payload['next']['final_verdict'] == 'NG'


def test_cpp_cancel_is_callable_during_execute_and_handle_is_reusable(real_package, tmp_path):
    package, image = real_package
    native = tmp_path / 'native'
    built = subprocess.run([sys.executable, str(package / 'native_runtime/build_native.py'), '--output', str(native)],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    process = subprocess.run([str(native / 'vision_cancel_demo'), str(package), str(image)],
                             capture_output=True, text=True, encoding="utf-8", timeout=45)
    _assert_demo(process)


def test_csharp_cancel_is_callable_during_execute_and_handle_is_reusable(real_package, tmp_path):
    dotnet = shutil.which('dotnet') or os.environ.get('VISION_TEST_DOTNET')
    if not dotnet:
        pytest.skip('A .NET 8 SDK is required for the C# compilation gate')
    package, image = real_package
    native = tmp_path / 'native'
    csharp = native / 'csharp'
    built = subprocess.run([sys.executable, str(package / 'native_runtime/build_native.py'), '--output', str(native)],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    compiled = subprocess.run([dotnet, 'build', str(package / 'native_runtime/VisionRuntime.csproj'), '-o', str(csharp)],
                              capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    for artifact in native.glob('*modu_vision_runtime*'):
        if artifact.is_file():
            shutil.copyfile(artifact, csharp / artifact.name)
    process = subprocess.run([dotnet, str(csharp / 'VisionRuntime.dll'), str(package), '--cancel-demo', str(image)],
                             capture_output=True, text=True, encoding="utf-8", timeout=45)
    _assert_demo(process)


def test_interruption_reaps_the_actual_owned_child(monkeypatch):
    from backend.engine.runtime_deadline import execute_owned_process
    original = subprocess.Popen
    children = []
    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        if children:
            return process
        children.append(process)
        wait = process.wait
        interrupted = False
        def interrupt_once(*args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise KeyboardInterrupt()
            return wait(*args, **kwargs)
        process.wait = interrupt_once
        return process
    monkeypatch.setattr(subprocess, 'Popen', launch)
    try:
        with pytest.raises(KeyboardInterrupt):
            execute_owned_process([sys.executable, '-c', 'import time;time.sleep(30)'], deadline_ms=30000)
        assert children[0].poll() is not None, 'Interrupted CLI must reap its owned inference child'
    finally:
        # Preserve cleanup even when reproducing the pre-fix failure.
        for process in children:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
