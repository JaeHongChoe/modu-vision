import { Sha256Stream } from '../utils/sha256Stream';
import type { ArtifactRef, ArtifactUpload } from './api';

/** The server accepts at most 4 MiB per chunk. */
export const UPLOAD_CHUNK = 4 * 1024 * 1024;
const HASH_SLICE = 8 * 1024 * 1024;

export interface UploadClient {
  beginUpload(data: { kind: 'source'; sha256: string; size_bytes: number }): Promise<{ upload: ArtifactUpload }>;
  uploadStatus(uploadId: string): Promise<{ upload: ArtifactUpload }>;
  uploadChunk(uploadId: string, offset: number, chunk: Blob, signal?: AbortSignal): Promise<{ upload: ArtifactUpload }>;
  completeUpload(uploadId: string): Promise<{ artifact_ref: ArtifactRef }>;
  cancelUpload(uploadId: string): Promise<unknown>;
}
export interface ArchiveProgress { phase: 'hashing' | 'uploading' | 'verifying'; done: number; total: number }
export interface UploadedArchive { artifact: ArtifactRef; sha256: string; size: number }
interface Options {
  onProgress?: (progress: ArchiveProgress) => void;
  signal?: AbortSignal;
  /** Consecutive failed chunks tolerated before the upload stops (each retry resumes from the server's offset). */
  retries?: number;
  wait?: (milliseconds: number) => Promise<void>;
}

const stopped = (signal?: AbortSignal) => {
  if (signal?.aborted) throw Object.assign(new Error('업로드를 중지했습니다.'), { name: 'AbortError' });
};
// A refused request (bad size, quota, permission) will not succeed on retry; a dropped connection or a busy server may.
const retryable = (caught: unknown) => {
  const status = (caught as { status?: number } | null)?.status;
  return status === undefined || status === 408 || status === 429 || status >= 500;
};

/** SHA-256 of a file read slice by slice, so a multi-gigabyte archive never sits in memory at once. */
export async function hashBlob(file: Blob, onProgress?: (done: number, total: number) => void, signal?: AbortSignal,
  slice = HASH_SLICE): Promise<string> {
  const digest = new Sha256Stream();
  for (let offset = 0; offset < file.size; offset += slice) {
    stopped(signal);
    const end = Math.min(file.size, offset + slice);
    digest.update(new Uint8Array(await file.slice(offset, end).arrayBuffer()));
    onProgress?.(end, file.size);
  }
  return digest.hex();
}

/**
 * Upload a dataset ZIP as a verified artifact: hash it locally, reserve the upload with that digest, send 4 MiB chunks
 * (a failed chunk resumes from the offset the server committed), and complete it; the server checks the digest again.
 */
export async function uploadArchive(file: File, client: UploadClient, options: Options = {}): Promise<UploadedArchive> {
  const { onProgress, signal, retries = 3, wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms)) } = options;
  if (!/\.zip$/i.test(file.name)) throw new Error('ZIP 파일만 올릴 수 있습니다.');
  onProgress?.({ phase: 'hashing', done: 0, total: file.size });
  const sha256 = await hashBlob(file, (done, total) => onProgress?.({ phase: 'hashing', done, total }), signal);
  stopped(signal);
  const { upload } = await client.beginUpload({ kind: 'source', sha256, size_bytes: file.size });
  try {
    let offset = upload.offset;
    let failures = 0;
    onProgress?.({ phase: 'uploading', done: offset, total: file.size });
    while (offset < file.size) {
      stopped(signal);
      const end = Math.min(file.size, offset + UPLOAD_CHUNK);
      try {
        offset = (await client.uploadChunk(upload.id, offset, file.slice(offset, end), signal)).upload.offset;
        failures = 0;
      } catch (caught) {
        if (signal?.aborted || !retryable(caught) || ++failures > retries) throw caught;
        await wait(500 * failures);
        try {
          offset = (await client.uploadStatus(upload.id)).upload.offset;  // resume where the server committed
        } catch (status) {
          if (!retryable(status)) throw status;  // still unreachable: the next attempt counts as another failure
        }
      }
      onProgress?.({ phase: 'uploading', done: offset, total: file.size });
    }
    onProgress?.({ phase: 'verifying', done: file.size, total: file.size });
    const { artifact_ref } = await client.completeUpload(upload.id);
    return { artifact: artifact_ref, sha256, size: file.size };
  } catch (caught) {
    // A stopped or failed upload releases its reservation (staged bytes and project quota) instead of holding it until
    // it expires; a failure to cancel does not hide the original error.
    await client.cancelUpload(upload.id).catch(() => undefined);
    throw caught;
  }
}
