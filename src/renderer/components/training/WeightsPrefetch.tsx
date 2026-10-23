/** Download a model's official pretrained weights now, with progress, instead of silently during the first training
 *  (audit F-12/A5). The files go to the same caches training reads; a failure says what to do and nothing is substituted. */
import { useEffect, useRef, useState } from 'react';
import { request } from '../../services/api';
import { programButton } from './ProgramWorkbenchControls';

export type PrefetchStatus = { model: string; state: 'idle' | 'downloading' | 'done' | 'failed'; bytes: number; total: number | null; error: string | null };

/** DINOv3 (Hugging Face) and YOLO (official asset) weights can be fetched ahead; others come from their own setup. */
export const prefetchSupported = (model: string) => /^dinov3_vit[sbl]16$/.test(model) || /^yolo26[ns]$/.test(model);

export function prefetchProgressText(status: PrefetchStatus): string {
  const mb = (value: number) => `${(value / (1024 * 1024)).toFixed(1)}MB`;
  if (status.state === 'downloading') return status.total ? `받는 중 ${mb(status.bytes)} / ${mb(status.total)} (${Math.floor(100 * status.bytes / status.total)}%)` : `받는 중 ${mb(status.bytes)}`;
  if (status.state === 'done') return `받기 완료 · ${mb(status.total || status.bytes)}`;
  if (status.state === 'failed') return status.error || '받지 못했습니다.';
  return '';
}

export function WeightsPrefetch({ model, onDone }: { model: string; onDone: () => void }) {
  const [status, setStatus] = useState<PrefetchStatus | null>(null);
  const [error, setError] = useState('');
  const live = useRef(true);
  useEffect(() => { live.current = true; setStatus(null); setError(''); return () => { live.current = false; }; }, [model]);
  const poll = async () => {
    while (live.current) {
      const value = await request<PrefetchStatus>(`/api/training-workspace/prefetch-weights?model=${encodeURIComponent(model)}`);
      if (!live.current) return;
      setStatus(value);
      if (value.state !== 'downloading') { if (value.state === 'done') onDone(); return; }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  };
  const start = async () => {
    setError('');
    try {
      setStatus(await request<PrefetchStatus>('/api/training-workspace/prefetch-weights', { method: 'POST', body: JSON.stringify({ model }) }));
      await poll();
    } catch (cause) { if (live.current) setError(cause instanceof Error ? cause.message : String(cause)); }
  };
  const downloading = status?.state === 'downloading';
  return <div className="mt-2 rounded border border-slate-600 p-2">
    <p>첫 학습 때 인터넷에서 공식 사전학습 가중치를 받습니다{model.startsWith('dinov3_vits') ? ' (약 83MB)' : ''}. 미리 받아 두면 학습이 바로 시작되고, 받는 동안 진행률을 볼 수 있습니다.</p>
    <button type="button" disabled={downloading} onClick={() => void start()} className={`${programButton} mt-2`}>{downloading ? '받는 중…' : '가중치 미리 받기'}</button>
    {status && status.state !== 'idle' && <p role={status.state === 'failed' ? 'alert' : 'status'} className={`mt-2 ${status.state === 'failed' ? 'text-amber-200' : 'text-slate-200'}`}>{prefetchProgressText(status)}</p>}
    {downloading && status?.total ? <progress aria-label="가중치 받기 진행률" className="mt-1 w-full" max={status.total} value={status.bytes} /> : null}
    {error && <p role="alert" className="mt-2 text-amber-200">{error}</p>}
  </div>;
}
