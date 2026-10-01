import {useEffect,useRef,type ReactNode} from 'react';
export function WorkspaceDialog({title,onClose,children}:{title:string;onClose:()=>void;children:ReactNode}) {
  const ref=useRef<HTMLDivElement>(null);
  useEffect(()=>{const previous=document.activeElement as HTMLElement|null;const node=ref.current;node?.querySelector<HTMLElement>('button')?.focus();return()=>previous?.focus();},[]);
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" onMouseDown={e=>{if(e.target===e.currentTarget)onClose();}}>
    <div ref={ref} role="dialog" aria-modal="true" aria-label={title} className="product-workbench flex max-h-[90vh] w-full max-w-6xl flex-col rounded-xl border border-slate-600 bg-[#101722] shadow-2xl" onKeyDown={e=>{if(e.key==='Escape'){e.stopPropagation();onClose();}if(e.key==='Tab'){const nodes=[...(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary,[tabindex="0"]')||[])].filter(n=>n.offsetParent!==null);const first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}}}>
      <header className="flex items-center justify-between border-b border-slate-700 px-5 py-3"><h2 className="text-lg font-semibold">{title}</h2><button aria-label={`${title} 닫기`} className="rounded border border-slate-600 px-3 py-1.5" onClick={onClose}>닫기</button></header>
      <div className="min-h-0 overflow-auto p-4">{children}</div>
    </div>
  </div>;
}
