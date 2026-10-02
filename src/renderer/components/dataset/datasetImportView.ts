import type { DatasetImportView, DatasetQuickValidation, DatasetRevisionRow } from '../../services/api';

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
    return `손상 이미지 ${receipt.error_count}장${folders} 때문에 거부 정책이 이 버전을 막았습니다. 원본을 고치거나 제외 정책으로 다시 실행하세요.`;
  }
  if (receipt.image_count === 0) return '이미지가 하나도 없는 버전은 채택할 수 없습니다.';
  const row = revisions.find((item) => item.revision_id === receipt.revision_id);
  if (row?.active) return '이미 활성 버전입니다.';
  return null;
}

/** What the quick folder inspection may claim; a sample or partial check never reads as "no corrupt images". */
export function quickValidationNotice(validation: DatasetQuickValidation | undefined | null): string | null {
  if (!validation || !validation.requested || validation.complete) return null;
  if (validation.scope.startsWith('sampled')) {
    return `빠른 확인은 처음 ${validation.checked_images}장만 열어 봤습니다. 전체 이미지 검증은 '검증된 데이터 버전'에서 실행하세요.`;
  }
  return `빠른 확인 범위가 일부입니다(${validation.checked_images}장): ${validation.scope}. 전체 검증은 '검증된 데이터 버전'에서 실행하세요.`;
}

/** The source the server will read is the project's registered one; say so when the screen shows another folder. */
export function sourceMismatchNotice(shownFolder: string, registeredSource: string | null | undefined): string | null {
  if (!registeredSource) return '프로젝트에 등록된 원본 폴더가 없습니다. 데이터 폴더를 다시 선택해 저장한 뒤 실행하세요.';
  const normalize = (value: string) => value.replace(/[\\/]+$/, '');
  return normalize(shownFolder) === normalize(registeredSource)
    ? null : '화면의 폴더 경로와 프로젝트에 등록된 원본 경로가 다르게 표시됩니다(바로가기 경로일 수도 있습니다). 가져오기는 등록된 원본을 읽습니다.';
}
