import type { ReactNode } from 'react';

export type StatusTone = 'success' | 'warning' | 'danger' | 'neutral' | 'info';
const symbols: Record<StatusTone, string> = { success: '✓', warning: '!', danger: '×', neutral: '—', info: 'i' };
export function StatusBadge({ tone = 'neutral', children }: { tone?: StatusTone; children: ReactNode }) {
  return <span className="workspace-badge" data-tone={tone}><span aria-hidden="true">{symbols[tone]}</span>{children}</span>;
}
