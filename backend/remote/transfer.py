"""Resumable artifact HTTP transfer using the same authenticated server contract.

The caller supplies an authenticated HTTP client and project headers. No host
paths, credentials, or hash-only access bypass are introduced by this adapter.
"""
import hashlib
import os
from pathlib import Path
import tempfile

from backend.contracts.context import ArtifactRef
from backend.engine.artifact_store import CHUNK_BYTES


class ArtifactTransfer:
    def __init__(self, client, *, headers, chunk_bytes=CHUNK_BYTES):
        if not 1 <= chunk_bytes <= CHUNK_BYTES:
            raise ValueError('Transfer chunk size must be between 1 byte and 4 MiB')
        self.client, self.headers, self.chunk_bytes = client, dict(headers), chunk_bytes

    def upload(self, source, expected_hash, size_bytes, *, kind='source', upload_id=None):
        if upload_id is None:
            response = self.client.post('/api/artifacts/uploads', headers=self.headers,
                                        json={'kind': kind, 'sha256': expected_hash, 'size_bytes': size_bytes})
        else:
            response = self.client.get('/api/artifacts/uploads/' + upload_id, headers=self.headers)
        response.raise_for_status()
        upload = response.json()['upload']
        if (upload['sha256'], upload['size_bytes'], upload['kind']) != (expected_hash, size_bytes, kind):
            raise ValueError('Resumed upload does not match this source contract')
        offset = upload['offset']; source.seek(offset)
        while offset < size_bytes:
            data = source.read(min(self.chunk_bytes, size_bytes - offset))
            if not data:
                raise ValueError('Transfer source ended before its declared size')
            response = self.client.put('/api/artifacts/uploads/' + upload['id'], params={'offset': offset},
                                       headers={**self.headers, 'Content-Type': 'application/octet-stream'}, content=data)
            response.raise_for_status()
            next_offset = response.json()['upload']['offset']
            if next_offset != offset + len(data):
                raise ValueError('Server did not acknowledge the submitted upload offset')
            offset = next_offset
        response = self.client.post('/api/artifacts/uploads/' + upload['id'] + '/complete', headers=self.headers)
        response.raise_for_status()
        ref = ArtifactRef.model_validate(response.json()['artifact_ref'])
        if ref.sha256 != expected_hash:
            raise ValueError('Published artifact does not match the uploaded hash')
        return ref

    def download(self, ref, destination, *, partial=None):
        ref = ref if isinstance(ref, ArtifactRef) else ArtifactRef.model_validate(ref)
        destination = Path(destination)
        if destination.is_symlink() or any(parent.is_symlink() for parent in destination.parents):
            raise ValueError('Download destination must not be a symbolic link')
        destination.parent.mkdir(parents=True, exist_ok=True)
        owned = partial is None
        if owned:
            fd, name = tempfile.mkstemp(prefix='.artifact-download-', dir=destination.parent)
            os.close(fd); path = Path(name)
        else:
            path = Path(partial)
            if path.is_symlink() or path == destination or path.parent.resolve() != destination.parent.resolve():
                raise ValueError('Resume file must be a distinct owned file beside the destination')
            path.touch(exist_ok=True)
        offset = path.stat().st_size
        # Re-read one byte even when the previous attempt finished fsync and
        # crashed before rename. Every retry still reaches authenticated content
        # verification; bytes=size- would otherwise return 416 forever.
        resume_start = max(0, offset - 1)
        headers = {**self.headers, **({'Range': f'bytes={resume_start}-'} if offset else {})}
        try:
            with self.client.stream('GET', f'/api/artifacts/{ref.id}/content',
                                    params={'revision': ref.revision, 'sha256': ref.sha256}, headers=headers) as response:
                response.raise_for_status()
                if (response.headers.get('x-artifact-id'), response.headers.get('x-artifact-revision'),
                    response.headers.get('x-artifact-sha256')) != (ref.id, str(ref.revision), ref.sha256):
                    raise ValueError('Downloaded content has a different artifact receipt')
                if offset and (response.status_code != 206 or not response.headers.get('content-range', '').startswith(f'bytes {resume_start}-')):
                    raise ValueError('Server did not acknowledge the requested download offset')
                overlap = None
                if offset:
                    with path.open('rb') as previous:
                        previous.seek(resume_start)
                        overlap = previous.read(1)
                with path.open('ab') as target:
                    for block in response.iter_bytes(self.chunk_bytes):
                        if overlap is not None and block:
                            if block[:1] != overlap:
                                raise ValueError('Resumed content does not match the retained partial')
                            block, overlap = block[1:], None
                        target.write(block)
                    if overlap is not None:
                        raise ValueError('Server did not return the resumed verification byte')
                    target.flush(); os.fsync(target.fileno())
            digest = hashlib.sha256()
            with path.open('rb') as source:
                for block in iter(lambda: source.read(CHUNK_BYTES), b''):
                    digest.update(block)
            if digest.hexdigest() != ref.sha256:
                raise ValueError('Downloaded artifact content hash does not match')
            os.replace(path, destination)
        finally:
            if owned:
                path.unlink(missing_ok=True)
        return destination
