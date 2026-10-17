"""Physical GPU UUID spelling controls; no CUDA call, network or inference.

PyTorch 2.6 returns a _CUuuid object whose original str conversion produces a
bare full UUID. Root executes these source controls; they provide no actual GPU,
worker ownership, CUDA availability, native Run, or model-quality acceptance.
"""
from types import SimpleNamespace
import pytest
from backend.engine import required_flow_passive_capture as capture

EXPECTED = 'GPU-680335ce-4e6c-c68f-e3ee-7135586d4a04'
BARE = EXPECTED.removeprefix('GPU-')


class CUDAUuidLike:
    """Only the original string-producing shape; no torch/CUDA dependency."""
    def __init__(self, text):
        self.text = text
    def __str__(self):
        return self.text


class EqualString(str):
    def __eq__(self, other):
        return True


@pytest.mark.parametrize('value', [
    EXPECTED, BARE, BARE.upper(), 'GPU-'+BARE.upper(),
    CUDAUuidLike(BARE), CUDAUuidLike(BARE.upper()), EqualString(EXPECTED),
])
def test_same_full_physical_uuid_forms_preserve_canonical_observation(value):
    assert capture._observed_selected_gpu_uuid(SimpleNamespace(uuid=value)) == EXPECTED


@pytest.mark.parametrize('value', [
    None, '', 'unavailable', 2, b'680335ce-4e6c-c68f-e3ee-7135586d4a04',
    ['680335ce-4e6c-c68f-e3ee-7135586d4a04'], {'uuid': EXPECTED},
    EqualString('unavailable'),
    'GPU-680335ce-4e6c-c68f-e3ee-7135586d4a05',
    '680335ce-4e6c-c68f-e3ee-7135586d4a05',
    'MIG-'+BARE, 'MIG-'+EXPECTED+'/1/0',
    EXPECTED+' ', ' '+EXPECTED, EXPECTED+'\n',
    BARE.replace('-', ''), 'gpu-'+BARE, 'GPU-'+BARE[:8],
    CUDAUuidLike('680335ce-4e6c-c68f-e3ee-7135586d4a05'),
    CUDAUuidLike('MIG-'+BARE), CUDAUuidLike('unavailable'),
])
def test_unproven_different_malformed_or_mig_identity_still_refuses(value):
    with pytest.raises(ValueError, match='Original selected GPU UUID cannot be observed exactly'):
        capture._observed_selected_gpu_uuid(SimpleNamespace(uuid=value))


def test_missing_uuid_property_still_refuses():
    with pytest.raises(ValueError, match='Original selected GPU UUID cannot be observed exactly'):
        capture._observed_selected_gpu_uuid(SimpleNamespace())
