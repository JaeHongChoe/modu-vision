import React from 'react';
import { useProjectStore } from '../../stores/useProjectStore';

/** In the simple view, management panels a first run does not need wait behind one closed line; 모두 보기 shows them
 * as before. The children render either way, so every feature stays reachable. */
export const SimpleViewFold: React.FC<{ label: string; hint?: string; children: React.ReactNode }> = ({ label, hint, children }) => {
  const full = useProjectStore((state) => state.viewMode) === 'full';
  if (full) return <>{children}</>;
  return (
    <details className="border-b border-[#2B3547] bg-[#0E121A] px-4 py-1.5 text-xs text-slate-300">
      <summary className="cursor-pointer select-none">
        <span className="font-semibold text-slate-200">{label}</span>
        {hint && <span className="ml-2 text-slate-500">{hint}</span>}
      </summary>
      <div className="mt-1.5 -mx-4 border-t border-[#2B3547]">{children}</div>
    </details>
  );
};
