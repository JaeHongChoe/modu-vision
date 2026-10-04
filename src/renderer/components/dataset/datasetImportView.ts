import type { DatasetImportView, DatasetQuickValidation, DatasetRevisionReceipt, DatasetRevisionRow, DatasetSourceStatus } from '../../services/api';

const ENDED = new Set(['completed', 'failed', 'aborted', 'interrupted']);
const STATE_TEXT: Record<string, string> = {
  accepted: '대기 중', running: '이미지 읽는 중', completed: '검증 완료 · 채택 전', failed: '실패',
  aborted: '중지됨 · 버전 없음', interrupted: '중단됨 · 다시 실행 필요',
};

export const importEnded = (view: DatasetImportView | null) => Boolean(view && ENDED.has(view.state));

/** Progress as the server reports it: no percentage until the total is known. */
export function importProgress(view: DatasetImportView | null): { text: string; percent: number | null } {
  const progress = view?.progress;
  if (!view) return { text: '', percent: null };
  if (view.state === 'running' && progress?.phase === 'recording') {
    return { text: '검증 완료 · 결과 기록 대기 (다음 시작 때 자동으로 완료됩니다)', percent: null };
  }
  if (view.state === 'running' && !progress) {
    return { text: '실행 중 · 진행 상황은 이 작업을 실행하는 앱에서만 보입니다', percent: null };
  }
  if (!progress || !progress.total_known || !progress.total) {
    return { text: progress?.phase === 'listing' ? '이미지 목록 확인 중' : STATE_TEXT[view.state] || view.state, percent: null };
  }
  const processed = Math.min(progress.processed || 0, progress.total);
  return { text: `${processed.toLocaleString()} / ${progress.total.toLocaleString()}장 확인`, percent: Math.floor((processed / progress.total) * 100) };
}

export function importStatusText(view: DatasetImportView | null): string {
  if (!view) return '';
  if (view.state === 'failed') return `실패: ${view.result?.error?.message || '원인 미기록'}`;
  if (view.cancel_requested && !importEnded(view)) return '중지 요청됨 · 현재 파일 이후 멈춥니다';
  if (view.cancel_requested && view.state === 'completed') return '중지 요청 전에 검증이 끝났습니다 · 채택 여부를 직접 정하세요';
  return STATE_TEXT[view.state] || view.state;
}

/** A revision can be adopted only when this completed import published it and the reject policy did not hold it. */
export function acceptBlocker(view: DatasetImportView | null, revisions: DatasetRevisionRow[]): string | null {
  const receipt = view?.result?.revision;
  if (!view || view.state !== 'completed' || !receipt) return '완료된 가져오기의 결과만 채택할 수 있습니다.';
  if (receipt.state === 'rejected') {
    const folders = receipt.unreadable_folders ? ` · 읽지 못한 폴더 ${receipt.unreadable_folders}개` : '';
    return `손상·주석 오류 ${receipt.error_count}장${folders} 때문에 거부 정책이 이 버전을 막았습니다. 원본을 고치거나 제외 정책으로 다시 실행하세요.`;
  }
  if (receipt.image_count === 0) return '이미지가 하나도 없는 버전은 채택할 수 없습니다.';
  if (receipt.valid_count === 0) return '정상 이미지가 하나도 없는 버전은 채택할 수 없습니다(모두 제외됩니다).';
  const row = revisions.find((item) => item.revision_id === receipt.revision_id);
  if (row?.active) return '이미 활성 버전입니다.';
  return null;
}

/** What a receipt says about the source's own annotation files; receipts sealed before they were recorded say so. */
export function annotationSummary(receipt: DatasetRevisionReceipt | null | undefined): string | null {
  if (!receipt) return null;
  if (receipt.annotated == null) return '이 버전은 주석 파일을 기록하기 전에 만들어졌습니다. 다시 검증하면 기록됩니다.';
  const effect = receipt.annotations_bind === false
    ? '이 작업은 폴더 라벨로 학습하므로 이미지를 제외하지 않음'
    : '손상 수에 포함, 제외 목록에 이유 표시';
  const errors = receipt.annotation_errors ? ` · 주석 오류 ${receipt.annotation_errors.toLocaleString()}장(${effect})` : '';
  return `주석 파일(LabelMe·COCO·YOLO)이 연결된 이미지 ${receipt.annotated.toLocaleString()}장${errors}`;
}

/** Duplicate groups as counts: same bytes, labels that differ, or splits that mix (train/test leakage). */
export function duplicateSummary(receipt: DatasetRevisionReceipt | null | undefined): string | null {
  if (!receipt || receipt.duplicate_groups == null) return null;
  if (!receipt.duplicate_groups) return '같은 내용의 이미지가 없습니다.';
  const parts = [`같은 내용 그룹 ${receipt.duplicate_groups.toLocaleString()}개(이미지 ${(receipt.duplicate_images || 0).toLocaleString()}장)`];
  if (receipt.conflicting_duplicates) parts.push(`라벨이 다른 그룹 ${receipt.conflicting_duplicates.toLocaleString()}개`);
  if (receipt.cross_split_duplicates) parts.push(`train/val/test가 섞인 그룹 ${receipt.cross_split_duplicates.toLocaleString()}개`);
  return `${parts.join(' · ')} · 중복은 지우지 않고 보고만 합니다.`;
}

/** Which source a job read, so a ZIP import is never mistaken for the registered folder shown beside it. */
export function importSourceText(view: DatasetImportView | null): string | null {
  if (!view?.source) return null;
  return view.source.artifact ? `읽은 원본: 업로드한 ZIP · ${view.source.artifact.sha256.slice(0, 12)}` : '읽은 원본: 등록된 원본 폴더';
}

/** The upload step of a ZIP import, as text (a percentage only while the size is known and non-zero). */
export function archiveProgressText(progress: { phase: 'hashing' | 'uploading' | 'verifying'; done: number; total: number } | null): string {
  if (!progress) return '';
  const percent = progress.total ? Math.floor((progress.done / progress.total) * 100) : 100;
  if (progress.phase === 'hashing') return `파일 확인 중(SHA-256) ${percent}%`;
  if (progress.phase === 'uploading') return `업로드 중 ${percent}% · 끊기면 저장된 위치부터 이어서 보냅니다`;
  return '서버에서 파일 해시 확인 중';
}

/** What the quick folder inspection may claim; a sample or partial check never reads as "no corrupt images". */
export function quickValidationNotice(validation: DatasetQuickValidation | undefined | null): string | null {
  if (!validation || !validation.requested || validation.complete) return null;
  if (validation.scope.startsWith('sampled')) {
    return `빠른 확인은 처음 ${validation.checked_images}장만 열어 봤습니다. 전체 이미지 검증은 '검증된 데이터 버전'에서 실행하세요.`;
  }
  return `빠른 확인 범위가 일부입니다(${validation.checked_images}장): ${validation.scope}. 전체 검증은 '검증된 데이터 버전'에서 실행하세요.`;
}

/** What the data step says about validation. "Complete" is said only when a full validation of this source and task is
 *  the active version, it read the whole source (no unreadable folder, no skipped link), and it still matches the source
 *  by the backend index's inventory and source-status check. Invalid files remain excluded and are named in the
 *  counts; a recorded unreadable file matches only while its error state still holds. The quick check may use a
 *  different inventory or open only some images, so it is a preview. A version with gaps, or older than the source,
 *  says so; without an answer for the active revision nothing is claimed. */
export function validationStatus(validation: DatasetQuickValidation | undefined | null, active: DatasetRevisionRow | null | undefined,
  source: string | null | undefined, task: string, current: DatasetSourceStatus | null): { tone: 'ok' | 'warn'; text: string } | null {
  const normalize = (value: string) => value.replace(/[\\/]+$/, '');
  const quick = quickValidationNotice(validation);
  const matches = active && active.active && active.state === 'prepared' && active.task === task && source && normalize(active.source_root) === normalize(source);
  // The backend's answer for this very revision, by the same inventory definition the validation read (nqa2 review P2-1).
  if (!matches || !current || current.revision_id !== active.revision_id) return quick ? { tone: 'warn', text: quick } : null;
  const id = active.revision_id.slice(0, 12);
  if (active.unreadable_folders > 0 || active.skipped_links > 0) {
    const gaps = [active.unreadable_folders ? `읽지 못한 폴더 ${active.unreadable_folders}곳` : '', active.skipped_links ? `건너뛴 링크 ${active.skipped_links}개` : ''].filter(Boolean).join(' · ');
    return { tone: 'warn', text: `활성 버전 ${id}은 원본 일부를 읽지 못했습니다(${gaps}). 그 안의 이미지는 버전에 없습니다. 권한·링크를 확인한 뒤 '검증된 데이터 버전'에서 다시 검증하세요.${quick ? ` ${quick}` : ''}` };
  }
  if (!current.matches) {
    const changes = [current.added ? `추가 ${current.added}장` : '', current.removed ? `삭제 ${current.removed}장` : '',
      current.changed ? `바뀌었거나 확인되지 않은 ${current.changed}장` : '', current.gaps_changed ? '읽지 못한 폴더가 달라짐' : ''].filter(Boolean).join(' · ');
    return { tone: 'warn', text: `활성 버전 ${id}(${active.image_count}장)은 지금 원본(${current.images}장)과 다릅니다(${changes}). 바뀐 원본은 아직 전체 검증되지 않았습니다. '검증된 데이터 버전'에서 다시 검증하세요.` };
  }
  const excluded = active.error_count ? `, 손상·제외 ${active.error_count}장` : '';
  const preview = validation?.requested && !validation.complete ? ` 위의 빠른 확인(${validation.checked_images}장)은 미리보기 범위입니다.` : '';
  const basis = current.basis === 'stat_without_ctime' ? ' 원본 일치는 파일 크기·수정 시각으로 확인했습니다.' : '';
  return { tone: 'ok', text: `전체 검증 완료 · 활성 버전 ${id} · ${active.image_count}장 중 유효 ${active.valid_count}장${excluded}.${basis}${preview}` };
}

/** The source the server will read is the project's registered one; say so when the screen shows another folder. */
export function sourceMismatchNotice(shownFolder: string, registeredSource: string | null | undefined): string | null {
  if (!registeredSource) return '프로젝트에 등록된 원본 폴더가 없습니다. 데이터 폴더를 다시 선택해 저장한 뒤 실행하세요.';
  const normalize = (value: string) => value.replace(/[\\/]+$/, '');
  return normalize(shownFolder) === normalize(registeredSource)
    ? null : '화면의 폴더 경로와 프로젝트에 등록된 원본 경로가 다르게 표시됩니다(바로가기 경로일 수도 있습니다). 가져오기는 등록된 원본을 읽습니다.';
}

/** The panel header: "active" once the job's own revision is the active one. */
export function importHeadline(view: DatasetImportView | null, revisions: DatasetRevisionRow[]): string {
  const receipt = view?.result?.revision;
  if (view?.state === 'completed' && receipt && revisions.some((row) => row.revision_id === receipt.revision_id && row.active)) {
    return '검증 완료 · 활성 버전';
  }
  return importStatusText(view);
}

// One idempotency key per intended import, kept for the app session (a retried click or a reopened panel returns
// the same job); dropped once the start succeeded or when the project, source or settings change.
const pendingKeys = new Map<string, string>();
export function importKeyFor(settings: string, make: () => string): string {
  const key = pendingKeys.get(settings) || make();
  pendingKeys.set(settings, key);
  return key;
}
export function clearImportKey(settings: string): void { pendingKeys.delete(settings); }

/** How a revision row names the import job that sealed it. */
export function revisionJobLabel(row: Pick<DatasetRevisionRow, 'publication_key'>): string {
  return row.publication_key ? `작업 ${row.publication_key.slice(0, 8)}` : '작업 기록 없음';
}
