"""Download a model's official pretrained weights before the first training, with progress (audit F-12 / A5).

The first DINOv3 training used to download about 83 MB while the screen only said "running", and an offline machine
stopped with an English error. Step 3 can now start the same download on request and show its progress. The files are
the same official ones training would fetch, into the same caches (Hugging Face hub cache for DINOv3, the torch hub
checkpoint folder for YOLO); nothing else is downloaded and nothing is substituted when the download fails.
"""
from __future__ import annotations

import inspect
import io
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

_lock = threading.Lock()
_jobs: Dict[str, dict] = {}


def supported(model: str) -> bool:
    from backend.engine.model_backbones import DINO_MODELS, YOLO_MODELS
    return model in DINO_MODELS or model in YOLO_MODELS


def prefetch_status(model: str) -> dict:
    with _lock:
        job = _jobs.get(model)
        return dict(job) if job else {'model': model, 'state': 'idle', 'bytes': 0, 'total': None, 'error': None, 'path': None}


def start_prefetch(model: str, downloader: Optional[Callable[[str, Callable[[int, Optional[int]], None]], Path]] = None) -> dict:
    """Start (or join) the download of ``model``'s official weights; returns the current status at once."""
    if not supported(model):
        raise ValueError('이 모델은 미리 받기를 지원하지 않습니다. 첫 학습 때 받거나 공식 가중치 파일을 지정하세요.')
    with _lock:
        job = _jobs.get(model)
        if job and job['state'] == 'downloading':
            return dict(job)
        job = {'model': model, 'state': 'downloading', 'bytes': 0, 'total': None, 'error': None, 'path': None,
               'started_at': time.time()}
        _jobs[model] = job
    thread = threading.Thread(target=_run, args=(model, job, downloader or _download), daemon=True,
                              name=f'weights-prefetch-{model}')
    thread.start()
    return dict(job)


def _run(model: str, job: dict, downloader) -> None:
    def progress(done: int, total: Optional[int]) -> None:
        with _lock:
            job['bytes'] = int(done)
            if total:
                job['total'] = int(total)
    try:
        path = Path(downloader(model, progress))
        size = path.stat().st_size
        with _lock:
            job.update({'state': 'done', 'path': str(path), 'bytes': size, 'total': job['total'] or size})
    except Exception as exc:  # the message is the user's next step; the type stays for support
        with _lock:
            job.update({'state': 'failed', 'error': (
                '사전학습 가중치를 받지 못했습니다. 인터넷 연결(또는 Hugging Face 접근)을 확인하고 다시 시도하거나, 공식 '
                f"가중치 파일을 '공식 사전학습 가중치 가져오기'로 지정하세요. ({type(exc).__name__})")})


def _download(model: str, progress: Callable[[int, Optional[int]], None]) -> Path:
    from backend.engine.model_backbones import DINO_MODELS, _yolo_weights
    if model in DINO_MODELS:
        from huggingface_hub import hf_hub_download
        from tqdm.auto import tqdm

        class Progress(tqdm):
            """Counts bytes even when progress bars are disabled, and never writes to the terminal."""
            def __init__(self, *args, **kwargs):
                kwargs['file'] = io.StringIO()
                super().__init__(*args, **kwargs)
                self.seen = int(kwargs.get('initial') or 0)
                progress(self.seen, self.total)

            def update(self, n=1):
                self.seen += int(n or 0)
                progress(self.seen, self.total)
                return super().update(n)

        options = {'tqdm_class': Progress} if 'tqdm_class' in inspect.signature(hf_hub_download).parameters else {}
        return Path(hf_hub_download(repo_id=f'timm/{DINO_MODELS[model]}', filename='model.safetensors', **options))
    path, _, _ = _yolo_weights(model, None, None)  # small file; the ultralytics helper reports no byte progress
    return Path(path)
