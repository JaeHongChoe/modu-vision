import type { LibraryImage, LibraryResolution, LibrarySelection } from '../../services/api';
import type { SelectedInspectionImage } from '../../types';

/** The inspection image a library row stands for, with the identity that later resolves it. */
export function selectionFromImage(item: LibraryImage): SelectedInspectionImage {
  return {
    source: 'dataset', imagePath: item.file_path, imageId: item.image_uuid, imageUuid: item.image_uuid, sha256: item.sha256,
    relativePath: item.relative_path, fileName: item.file_name,
    thumbnailUrl: `/api/dataset/thumbnail/${encodeURIComponent(item.file_name)}?file_path=${encodeURIComponent(item.file_path)}`,
  };
}

/** What is sent to resolve a saved choice; only choices made from a validated revision have one. */
export function identityOf(selected: SelectedInspectionImage | null | undefined): LibrarySelection | null {
  if (!selected || selected.source !== 'dataset' || !selected.imageUuid || !selected.relativePath) return null;
  return { image_uuid: selected.imageUuid, sha256: selected.sha256 ?? null, relative_path: selected.relativePath };
}

const storageKey = (projectId: string) => `modu.inspectionImage.${projectId}`;

/** The last confirmed choice for this project, kept in this viewer's browser (a convenience: resolved before use). */
export function rememberSelection(projectId: string, selected: SelectedInspectionImage): void {
  if (!identityOf(selected)) return;
  try { window.localStorage.setItem(storageKey(projectId), JSON.stringify(selected)); } catch { /* storage may be unavailable */ }
}

export function recallSelection(projectId: string): SelectedInspectionImage | null {
  try {
    const raw = window.localStorage.getItem(storageKey(projectId));
    const parsed = raw ? JSON.parse(raw) as SelectedInspectionImage : null;
    return identityOf(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/** What the user must know about a saved choice after the source changed; null when it still names the same bytes. */
export function resolutionNotice(result: LibraryResolution | null | undefined): string | null {
  if (!result || result.status === 'found') return null;
  if (result.status === 'changed') {
    return `저장된 선택 ${result.relative_path} 자리에 다른 내용의 파일이 있습니다. 같은 이미지가 아니므로 다시 선택하세요.`;
  }
  if (result.status === 'unreadable') {
    return `저장된 선택 ${result.relative_path}을(를) 현재 데이터 버전에서 읽지 못했습니다(검증 때 열 수 없었음). 파일을 확인하고 다시 검증하거나 다른 이미지를 고르세요.`;
  }
  if (result.status === 'moved') {
    const first = result.candidates[0]?.relative_path;
    const more = result.candidates.length > 1 ? ` 외 ${result.candidates.length - 1}곳(같은 내용)` : '';
    return `저장된 선택 ${result.relative_path}이(가) ${first}${more}(으)로 옮겨졌습니다. 확인한 뒤 직접 선택하세요.`;
  }
  return `저장된 선택 ${result.relative_path}을(를) 현재 데이터 버전에서 찾을 수 없습니다. 다시 선택하세요.`;
}
