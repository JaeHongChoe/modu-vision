import type {ProjectConfig} from '../services/api';

type Step = 1 | 2 | 3 | 4 | 5 | 6;
type ProjectView = Pick<ProjectConfig, 'id' | 'project_dir' | 'source_dataset_dir' | 'active_labelset_id' | 'task'>;

/** Local navigation belongs to this exact workspace, never to a process port. */
export function projectViewScope(project: ProjectView | null, apiIdentity: string): string {
  return JSON.stringify([apiIdentity, project?.id, project?.project_dir,
    project?.source_dataset_dir, project?.active_labelset_id || 'default', project?.task]);
}

export function rememberProjectStep(storage: Pick<Storage, 'setItem'> | undefined, project: ProjectView | null,
  apiIdentity: string, step: Step): void {
  if (!project?.source_dataset_dir) return;
  try { storage?.setItem(`vision-project-view:${projectViewScope(project, apiIdentity)}`, String(step)); }
  catch { /* Navigation remains usable when local storage is unavailable. */ }
}

export function readProjectStep(storage: Pick<Storage, 'getItem'> | undefined, project: ProjectView | null,
  apiIdentity: string): Step {
  if (!project?.source_dataset_dir) return 1;
  try {
    const value = Number(storage?.getItem(`vision-project-view:${projectViewScope(project, apiIdentity)}`));
    return Number.isInteger(value) && value >= 1 && value <= 6 ? value as Step : 1;
  } catch { return 1; }
}
