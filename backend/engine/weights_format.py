"""Pretrained weight files: tell a wrong or mismatched format before training reads it (audit F-13).

A safetensors file without its extension used to reach torch.load and fail mid-training with "UnpicklingError: invalid
load key" and a support message. Reading the first bytes is enough to name the problem in Korean. Nothing is renamed or
converted: the user's file stays as it is.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def sniff_weights_format(path: Path) -> str:
    """'safetensors', 'torch_zip' (torch.save), 'pickle' (legacy torch.save) or 'unknown', from the first bytes."""
    with Path(path).open('rb') as stream:
        head = stream.read(16)
    if head[:4] == b'PK\x03\x04':
        return 'torch_zip'
    if head[:1] == b'\x80':
        return 'pickle'
    if len(head) >= 9:
        header = int.from_bytes(head[:8], 'little')
        if 1 < header < 100_000_000 and head[8:9] == b'{':
            return 'safetensors'
    return 'unknown'


def weights_file_problem(path: Path) -> Optional[str]:
    """A Korean explanation when the file's content and name disagree or it is not a weight file; None when fine."""
    path = Path(path)
    kind = sniff_weights_format(path)
    suffix = path.suffix.lower()
    if kind == 'unknown':
        return (f"사전학습 가중치 파일로 읽을 수 없습니다 ({path.name}). 공식 배포 파일(.safetensors, .pt, .pth)을 그대로 "
                "지정하세요.")
    if kind == 'safetensors' and suffix != '.safetensors':
        return (f"파일 내용은 safetensors 형식인데 이름이 '{path.name}'입니다. 이름 끝을 .safetensors로 바꾼 뒤 다시 "
                "지정하세요 (원본 파일은 앱이 바꾸지 않습니다).")
    if kind != 'safetensors' and suffix == '.safetensors':
        return (f"이름은 .safetensors인데 내용은 PyTorch 저장 형식입니다 ({path.name}). 이름 끝을 .pt로 바꾸거나 "
                "공식 safetensors 파일을 지정하세요.")
    return None
