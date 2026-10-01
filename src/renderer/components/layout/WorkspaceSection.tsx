import { useId, type ReactNode } from 'react';

export function WorkspaceSection({ title, description, actions, children }: {
  title: string; description?: string; actions?: ReactNode; children: ReactNode;
}) {
  const titleId = useId();
  return <section className="workspace-section" aria-labelledby={titleId}>
    <header className="workspace-section__header"><div><h3 id={titleId}>{title}</h3>
      {description && <p className="workspace-description">{description}</p>}</div>{actions}</header>
    {children}
  </section>;
}
