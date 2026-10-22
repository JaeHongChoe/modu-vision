import { resolveApiUrl, type LabelSuggestion } from '../../services/api';

/** The image with the chosen draft candidates drawn over it; nothing here is a label until it is adopted. */
export function SuggestionPreview({ proposal, selectedIds }: { proposal: LabelSuggestion; selectedIds: Set<string> }) {
  const imageUrl = resolveApiUrl(
    `/api/dataset/thumbnail/${encodeURIComponent(proposal.image_id)}?file_path=${encodeURIComponent(proposal.image_path)}&size=1024`,
  );
  return (
    <div className="relative w-full overflow-hidden rounded-md border border-slate-700 bg-[#0B0E14]" style={{ aspectRatio: `${proposal.image_width} / ${proposal.image_height}` }}>
      <img src={imageUrl} alt="모델 라벨 제안 미리보기" className="absolute inset-0 h-full w-full object-contain" />
      <svg className="absolute inset-0 h-full w-full pointer-events-none" viewBox={`0 0 ${proposal.image_width} ${proposal.image_height}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
        {proposal.candidates.filter((item) => selectedIds.has(item.id)).map((item) => {
          const annotation = item.annotation;
          if (annotation.type === 'brush_mask' && annotation.mask_rle) return <image key={item.id} href={annotation.mask_rle} x={0} y={0} width={proposal.image_width} height={proposal.image_height} opacity={.55}/>;
          if (annotation.type === 'bbox' && annotation.bbox) {
            const [x1, y1, x2, y2] = annotation.bbox;
            return <rect key={item.id} x={x1} y={y1} width={x2 - x1} height={y2 - y1} fill="rgba(34,211,238,.12)" stroke="#22d3ee" strokeWidth={Math.max(2, proposal.image_width / 420)} />;
          }
          if (annotation.type === 'polygon' && annotation.polygon?.length) {
            return <polygon key={item.id} points={annotation.polygon.map(([x, y]) => `${x},${y}`).join(' ')} fill="rgba(245,158,11,.2)" stroke="#f59e0b" strokeWidth={Math.max(2, proposal.image_width / 420)} />;
          }
          return null;
        })}
      </svg>
    </div>
  );
}
