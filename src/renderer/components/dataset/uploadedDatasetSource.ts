import type {ArtifactRef, DatasetImportView, DatasetRevisionRow, DatasetSourceStatus, ProjectConfig, ProjectContext, ProjectSourceExpectation} from '../../services/api';
import type {VisionTask} from '../../types';

type Revisions = {active_revision: string | null; revisions: DatasetRevisionRow[]};
export type UploadedSourceScope = {
  key: string; ownerKey: string; context: ProjectContext; projectId: string; projectDir: string; task: VisionTask;
  labelsetId: string; source: string | null; expectedSource: ProjectSourceExpectation;
};
export type UploadedArchiveCandidate = {
  jobId: string; jobRevision: number; revisionId: string; manifest: string; root: string; artifact: ArtifactRef;
};

/** Index acceptance and choosing a project source are separate user actions. */
export function acceptedUploadedArchive(job: DatasetImportView | null, listed: Revisions, task: VisionTask): UploadedArchiveCandidate | null {
  const receipt = job?.state === 'completed' ? job.result?.revision : null;
  const source = job?.source, artifact = source?.artifact;
  if (!job || !receipt || receipt.state !== 'prepared' || receipt.image_count < 1 || receipt.valid_count < 1
      || !source?.root || !artifact || !artifact.id || !Number.isSafeInteger(artifact.revision) || artifact.revision < 1
      || !/^[a-f0-9]{64}$/.test(artifact.sha256) || !/^[a-f0-9]{64}$/.test(receipt.manifest_sha256)
      || listed.active_revision !== receipt.revision_id) return null;
  const rows = listed.revisions.filter(row => row.revision_id === receipt.revision_id);
  const row = rows.length === 1 ? rows[0] : null;
  if (!row || !row.active || row.state !== 'prepared' || row.task !== task || row.source_root !== source.root
      || row.publication_key !== job.job_id || row.manifest_sha256 !== receipt.manifest_sha256
      || row.image_count !== receipt.image_count || row.valid_count !== receipt.valid_count) return null;
  return {jobId: job.job_id, jobRevision: job.revision, revisionId: receipt.revision_id,
    manifest: receipt.manifest_sha256, root: source.root, artifact: {...artifact}};
}

type ProjectSource = Pick<ProjectConfig, 'id' | 'project_dir' | 'task' | 'active_labelset_id' | 'source_dataset_dir' | 'annotations_dir'>;
type Reads = {
  context: () => Promise<{project_context: ProjectContext}>;
  project: () => Promise<ProjectSource>;
  job: (jobId: string) => Promise<DatasetImportView>;
  revisions: () => Promise<Revisions>;
  sourceStatus: (revisionId: string) => Promise<Pick<DatasetSourceStatus, 'revision_id' | 'source_root' | 'matches'>>;
};
const contextKey = (context: ProjectContext) => JSON.stringify([context.workspace_id, context.project_id, context.actor_id, context.mode]);
const candidateKey = (candidate: UploadedArchiveCandidate) => JSON.stringify([candidate.jobId, candidate.jobRevision,
  candidate.revisionId, candidate.manifest, candidate.root, candidate.artifact.id, candidate.artifact.revision, candidate.artifact.sha256]);

/** Read current scoped server proofs immediately before the explicit source-selection callback. */
export async function useAcceptedUploadedSource({candidate, scope, isCurrent, reads, onUse}: {
  candidate: UploadedArchiveCandidate | null; scope: UploadedSourceScope; isCurrent: () => boolean;
  reads: Reads; onUse: (source: string) => Promise<void>;
}): Promise<void> {
  const current = () => {if (!isCurrent()) throw new Error('프로젝트나 서버 연결이 바뀌었습니다. 현재 프로젝트에서 ZIP을 다시 확인하세요.');};
  current();
  if (!candidate) throw new Error('채택된 ZIP 버전을 먼저 확인하세요.');
  const context = await reads.context(); current();
  if (contextKey(context.project_context) !== contextKey(scope.context)) throw new Error('ZIP 요청의 계정·프로젝트 문맥이 바뀌었습니다.');
  const project = await reads.project(); current();
  if (project.id !== scope.projectId || project.project_dir !== scope.projectDir || project.task !== scope.task
      || (project.active_labelset_id || 'default') !== scope.labelsetId || project.source_dataset_dir !== scope.source
      || project.annotations_dir !== scope.expectedSource.annotations_dir)
    throw new Error('프로젝트의 원본이나 라벨 세트가 바뀌었습니다. 현재 상태를 다시 불러오세요.');
  // The existing GET rejects jobs owned by another actor or opaque project namespace.
  const job = await reads.job(candidate.jobId); current();
  const listed = await reads.revisions(); current();
  const fresh = acceptedUploadedArchive(job, listed, scope.task);
  if (!fresh || candidateKey(fresh) !== candidateKey(candidate)) throw new Error('ZIP 가져오기나 활성 버전이 바뀌었습니다. 다시 확인하세요.');
  const status = await reads.sourceStatus(fresh.revisionId); current();
  if (status.revision_id !== fresh.revisionId || status.source_root !== fresh.root || !status.matches)
    throw new Error('검증한 ZIP 원본이 바뀌었습니다. 다시 검증한 뒤 선택하세요.');
  await onUse(fresh.root);
}
