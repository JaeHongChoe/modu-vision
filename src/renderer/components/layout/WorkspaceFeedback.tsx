import type { ReactNode } from 'react';

export function WorkspaceFeedback({ kind, title, description, action }: {
  kind: 'loading' | 'empty' | 'error'; title: string; description?: string; action?: ReactNode;
}) {
  return <div className="workspace-feedback" data-kind={kind} role={kind === 'error' ? 'alert' : kind === 'loading' ? 'status' : undefined}>
    <div><strong>{title}</strong>{description && <p className="workspace-description">{description}</p>}</div>{action}
  </div>;
}
