/** Split an explanation into its first sentence and the rest (product direction 5.4 item 6 (d)). */
export function splitExplanation(text: string): { first: string; rest: string } {
  const match = /^(.+?[.!?])\s+(\S[\s\S]*)$/.exec(text.trim());
  return match ? { first: match[1], rest: match[2] } : { first: text.trim(), rest: '' };
}

/** A panel's explanation as one line, with the rest behind 자세히: the person reads what matters first and opens the
 *  remainder only when they want it. The whole text stays in the page for keyboard and screen readers. */
export function Explain({ text, className = 'text-[11px] text-slate-500' }: { text: string; className?: string }) {
  const { first, rest } = splitExplanation(text);
  if (!rest) return <p className={className}>{first}</p>;
  return <details className={className}>
    <summary className="cursor-pointer">{first} <span className="text-slate-400 underline">자세히</span></summary>
    <p className="mt-1 leading-relaxed">{rest}</p>
  </details>;
}
