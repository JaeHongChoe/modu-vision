import { api } from '../../services/api';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useProjectStore } from '../../stores/useProjectStore';

/** Images without a label one press reads to tell whether the open image is one of them (the API's page maximum). */
export const UNLABELED_LOOKUP = 500;

/** The image step 2's '직접 라벨 달기' opens: none while the open image still has no label (or none is left), otherwise
 *  the first image without one. The listing keeps the gallery's source order and a labelled image leaves it, so each
 *  press after a save goes on to the next. */
export function unlabeledImageToOpen<T extends { file_path: string }>(unlabeled: readonly T[], openPath?: string | null): T | null {
  if (!unlabeled.length || (openPath && unlabeled.some((image) => image.file_path === openPath))) return null;
  return unlabeled[0];
}

/** Opens it through openImageForLabeling, which saves the open image's edits first and stays when that save fails.
 *  Resolves whether another image was opened; a failed read or a source/task change during it leaves the open image. */
export async function openUnlabeledImage(): Promise<boolean> {
  const { folderPath, hasSelectedFolder } = useDatasetStore.getState();
  const { task, isProjectBusy } = useProjectStore.getState();
  if (!hasSelectedFolder || !folderPath || isProjectBusy) return false;
  try {
    const listed = await api.dataset.getImages({ folder_path: folderPath, task, label_status: 'unlabeled', limit: UNLABELED_LOOKUP });
    // The source or task changed while it was read: the list belongs to another source.
    if (useDatasetStore.getState().folderPath !== folderPath || useProjectStore.getState().task !== task) return false;
    const next = unlabeledImageToOpen(listed.items || [], useAnnotationStore.getState().currentImage?.file_path);
    return next ? await useProjectStore.getState().openImageForLabeling(next.image_id, next.file_path) : false;
  } catch {
    return false;
  }
}
