import { useId, type ReactNode } from 'react';

type ControlProps = { id: string; 'aria-describedby'?: string; 'aria-invalid'?: true };
export function FormField({ id, label, hint, error, children }: {
  id?: string; label: string; hint?: string; error?: string;
  children: (props: ControlProps) => ReactNode;
}) {
  const generated = useId();
  const controlId = id || generated;
  const describedBy = [hint && `${controlId}-hint`, error && `${controlId}-error`].filter(Boolean).join(' ') || undefined;
  return <div className="workspace-field">
    <label htmlFor={controlId}>{label}</label>
    {children({ id: controlId, 'aria-describedby': describedBy, 'aria-invalid': error ? true : undefined })}
    {hint && <p id={`${controlId}-hint`} className="workspace-description">{hint}</p>}
    {error && <p id={`${controlId}-error`} role="alert" className="workspace-field__error">{error}</p>}
  </div>;
}
