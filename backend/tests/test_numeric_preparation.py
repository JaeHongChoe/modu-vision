"""CPU-only contract tests for the versioned numeric language."""
import importlib
from hashlib import sha256
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from backend.engine.input_preparation import bind_recipe, canonical_recipe, prepare_input, restore_mask, saved_recipe

FFT = ('spectrum = fftshift(fft2(image))\n'
       'mask = notch_mask(image, fx=.125, fy=0, radius=.02)\n'
       'result = real(ifft2(ifftshift(spectrum * mask)))')


def recipe(code='result = image + 10'):
    return {'version': 1, 'operations': [{'op': 'code', 'language': 'modu-numeric-v1', 'code': code}]}


def runtime():
    return importlib.import_module('backend.engine.numeric_preparation')


def test_code_pixels_mask_and_original_custody():
    image = np.full((16, 24, 3), 250, np.uint8)
    mask = np.arange(16 * 24, dtype=np.uint16).reshape(16, 24)
    before = image.copy(); labels = mask.copy()
    output, target, geometry = prepare_input(image, recipe(), task='segmentation', target=mask)
    assert np.all(output == 255)
    np.testing.assert_array_equal(image, before)
    np.testing.assert_array_equal(target, labels)
    np.testing.assert_array_equal(mask, labels)
    np.testing.assert_array_equal(restore_mask(target, geometry), labels)


def test_fft_removes_selected_frequency_preserving_dc_signal_and_channels():
    x = np.arange(64)
    signal = 10 * np.cos(2 * np.pi * 2 * x / 64)
    noise = 24 * np.cos(2 * np.pi * 8 * x / 64)
    source = np.stack([np.broadcast_to(base + signal + noise, (48, 64)) for base in (80, 128, 176)], axis=-1).round().astype(np.uint8)
    result, _, _ = prepare_input(source, recipe(FFT))
    before = np.fft.rfft(source.astype(float), axis=1)
    after = np.fft.rfft(result.astype(float), axis=1)
    assert np.abs(after[:, 8]).mean() < np.abs(before[:, 8]).mean() * .08
    np.testing.assert_allclose(result.mean((0, 1)), source.mean((0, 1)), atol=.6)
    assert np.abs(after[:, 2]).mean() > np.abs(before[:, 2]).mean() * .9


def test_binding_preserves_legacy_and_binds_numeric_runtime_exactly():
    old = {'version': 1, 'operations': [{'op': 'brightness', 'factor': 1.2}]}
    old_contract = {'implementation': 'modu-fixed-input-v1', 'image_interpolation': 'linear', 'mask_interpolation': 'nearest', 'tensor_range': '0..1', 'normalization': 'model_internal', 'rotation': 'counterclockwise', 'excluded_pixels': 'background'}
    assert bind_recipe(old, 'classification')['input_preparation_contract'] == old_contract
    bound = bind_recipe(recipe(), 'classification')
    assert bound['input_preparation_contract'] == {**old_contract, 'numeric_runtime': 'modu-numeric-v1'}
    assert bind_recipe(recipe('result=image+10'), 'classification')['input_recipe_sha256'] != bound['input_recipe_sha256']
    assert saved_recipe(bound, dict(bound)) == recipe()
    tampered = json.loads(json.dumps(bound)); tampered['input_preparation_contract'].pop('numeric_runtime')
    with pytest.raises(ValueError): saved_recipe(tampered, bound)
    with pytest.raises(ValueError): canonical_recipe(recipe(), 'detection')


@pytest.mark.parametrize('code', [
    'import os\nresult=image', 'result=image.__class__', 'result=image[::-1]',
    'for x in image:\n result=image', 'def f():\n return image\nresult=image',
    'result=__import__("os")', 'result=[x for x in image]', 'result=image**1000000',
    'image=0\nresult=image', 'result=fft2(image, **image)', 'result="hello"',
    'result=fftshift(image)', 'result=fft2(image)', 'result=image+notch_mask(image)',
    'result=fftshift(fft2(image))+fft2(image)', 'result=ifft2(fft2(image))',
    'result=1', 'result=unknown(image)',
])
def test_forbidden_syntax_domains_and_outputs_are_rejected(code):
    with pytest.raises(ValueError): prepare_input(np.zeros((16, 16, 3), np.uint8), recipe(code))


def test_entire_program_is_validated_before_native_work(monkeypatch):
    calls = []
    monkeypatch.setattr(np.fft, 'fft2', lambda *a, **k: calls.append(True))
    with pytest.raises(ValueError):
        prepare_input(np.zeros((16, 16, 3), np.uint8), recipe('s=fft2(image)\nresult=image.__class__'))
    assert calls == []


@pytest.mark.parametrize('code', ['result=exp(image+1000)', 'result=image/0', 'result=log(image-1)', 'result=sqrt(image-1)'])
def test_nonfinite_and_invalid_numeric_results_are_visible_errors(code):
    with pytest.raises(ValueError): prepare_input(np.zeros((16, 16, 3), np.uint8), recipe(code))


def test_pixel_limit_applies_at_code_operation_not_source_or_final_model_resize():
    source = np.zeros((1025, 1024, 3), np.uint8)
    with pytest.raises(ValueError, match='pixel'): prepare_input(source, recipe(), image_size=[16, 16])
    reduced = recipe(); reduced['operations'].insert(0, {'op': 'resize', 'width': 16, 'height': 16})
    result, _, _ = prepare_input(source, reduced)
    assert result.shape == (16, 16, 3) and not source.any()


@pytest.mark.parametrize('code', [
    '#'+('x'*8192)+'\nresult=image', '\n'*32+'result=image',
    '\n'.join(f'a{i}=image' for i in range(16))+'\nresult=image',
    'result='+('-'*25)+'image',
    'result='+ '+'.join(['image']*90),
    '\n'.join(f's{i}=fft2(image)' for i in range(5))+'\nresult=image',
])
def test_program_limits_rejected_without_native_fft(code, monkeypatch):
    calls = []
    monkeypatch.setattr(np.fft, 'fft2', lambda *a, **k: calls.append(True))
    with pytest.raises(ValueError): canonical_recipe(recipe(code))
    assert calls == []


def test_fft_allocation_is_checked_before_native_call(monkeypatch):
    module = runtime(); calls = []
    monkeypatch.setattr(module, 'MAX_RESULT_BYTES', 2000)
    monkeypatch.setattr(np.fft, 'fft2', lambda *a, **k: calls.append(True))
    with pytest.raises(ValueError, match='budget'):
        module.apply_numeric_code(np.zeros((8, 8, 3), np.uint8), 's=fft2(image)\nresult=image')
    assert calls == []


def test_fixed_math_and_scalar_broadcast():
    source = np.full((16, 16, 3), 25, np.uint8)
    result, _, _ = prepare_input(source, recipe('result=where(image>20, minimum(255, sqrt(image)*10), maximum(image, 0))'))
    assert np.all(result == 50)


def test_standalone_export_copies_and_runs_same_numeric_runtime(tmp_path):
    from backend.engine.exporter import _write_input_preparation_runtime
    _write_input_preparation_runtime(tmp_path, recipe(FFT))
    assert (tmp_path/'numeric_preparation.py').read_bytes() == Path(runtime().__file__).read_bytes()
    source = np.arange(16*16*3, dtype=np.uint8).reshape(16,16,3)
    expected, _, _ = prepare_input(source, recipe(FFT))
    expected_hash = sha256(expected.tobytes()).hexdigest()
    script = '''import sys,json,numpy as np
from hashlib import sha256
sys.path.insert(0,sys.argv[1])
from input_preparation import prepare_input,bind_recipe,saved_recipe
r=json.loads(sys.argv[2]); b=bind_recipe(r,'classification')
a,_,_=prepare_input(np.arange(16*16*3,dtype=np.uint8).reshape(16,16,3),saved_recipe(b,b))
assert sha256(a.tobytes()).hexdigest()==sys.argv[3] and 'backend' not in sys.modules
print('standalone numeric parity')'''
    result = subprocess.run([sys.executable, '-I', '-c', script, str(tmp_path), json.dumps(recipe(FFT)), expected_hash], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'standalone numeric parity'


@pytest.mark.parametrize('digits', [400, 4000])
def test_large_numeric_literal_is_a_visible_validation_error(digits):
    with pytest.raises(ValueError, match='constant'):
        canonical_recipe(recipe('result=image+' + '9' * digits))


def test_documented_fft_example_accepts_the_pixel_limit():
    source = np.full((1024, 1024, 3), 70, np.uint8)
    result, _, _ = prepare_input(source, recipe(FFT))
    np.testing.assert_array_equal(result, source)


def test_export_legacy_recipe_keeps_legacy_runtime_only(tmp_path):
    from backend.engine.exporter import _write_input_preparation_runtime
    _write_input_preparation_runtime(tmp_path, {'version':1,'operations':[{'op':'brightness','factor':1}]})
    assert (tmp_path/'input_preparation.py').is_file()
    assert not (tmp_path/'numeric_preparation.py').exists()


@pytest.mark.parametrize('code', [
    'result=real(ifft2(ifftshift(fftshift(fft2(image)))))',
    's=fftshift(fft2(image))\nf=notch_mask(image)\nresult=real(ifft2(ifftshift(s)*ifftshift(f)))',
])
def test_valid_frequency_domains_restore_spatial_image(code):
    source = np.full((16,16,3), 80, np.uint8)
    actual,_,_=prepare_input(source, recipe(code))
    np.testing.assert_array_equal(actual,source)


@pytest.mark.parametrize('arguments', ['fx=.51', 'fy=-.51', 'radius=0', 'radius=.251', 'fx=image'])
def test_invalid_notch_parameters_are_visible_errors(arguments):
    with pytest.raises(ValueError):
        prepare_input(np.zeros((16,16,3),np.uint8), recipe('s=fftshift(fft2(image))\nf=notch_mask(image,'+arguments+')\nresult=real(ifft2(ifftshift(s*f)))'))


def test_crlf_and_trailing_newline_use_physical_line_contract():
    code = ('a=image\r\n'*31)+'result=a\r\n'
    assert canonical_recipe(recipe(code)) == recipe(code)
    with pytest.raises(ValueError): canonical_recipe(recipe('\r\n'+code))


def test_statement_and_ast_budgets_are_separate_from_line_count():
    with pytest.raises(ValueError): canonical_recipe(recipe('a=image;'*32+'result=image'))
    with pytest.raises(ValueError): canonical_recipe(recipe(('a=image+image+image\n'*30)+'result=image'))
