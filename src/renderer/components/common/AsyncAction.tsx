import { useId, type ButtonHTMLAttributes } from 'react';

export function AsyncAction({ pending = false, pendingLabel = '처리 중…', disabledReason, children, disabled, className = '', ...props }:
  ButtonHTMLAttributes<HTMLButtonElement> & { pending?: boolean; pendingLabel?: string; disabledReason?: string }) {
  const reasonId = useId();
  return <span>
    <button {...props} type={props.type || 'button'} className={`workspace-button ${className}`}
      disabled={disabled || pending || Boolean(disabledReason)} aria-busy={pending || undefined}
      aria-describedby={[props['aria-describedby'], disabledReason && reasonId].filter(Boolean).join(' ') || undefined}>
      {pending ? pendingLabel : children}
    </button>
    {disabledReason && <span id={reasonId} className="workspace-description block">{disabledReason}</span>}
  </span>;
}
