import React, { useEffect, useState } from 'react';
import { request } from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';
import { useComputeStore } from '../../stores/useComputeStore';
type Lease = { job_id: string; host: string; selector: string; expires: number; uncertain: number; requires_reconciliation: boolean;memory_budget_mb:number;allow_sharing:boolean;task:string|null };
export const ComputeReservations: React.FC = () => {
  const projectId=useProjectStore(state=>state.project?.id);const revision=useComputeStore(state=>state.transportRevision);
  const [rows, setRows] = useState<Lease[]>([]); const [error, setError] = useState('');
  useEffect(() => {
    let valid = true;setRows([]);setError('');
    const refresh = () => request<{ reservations: Lease[] }>('/api/compute/reservations').then(result => { if (valid) { setRows(result.reservations); setError(''); } }).catch(cause => { if (valid) setError(cause.message || String(cause)); });
    void refresh(); const timer = window.setInterval(refresh, 5000);
    return () => { valid = false; window.clearInterval(timer); };
  }, [projectId,revision]);
  return <div className="mt-3 rounded border border-slate-700 p-3 text-xs"><h3 className="font-bold">공유 자원 예약</h3>{rows.length === 0 && <p className="mt-1 text-slate-400">예약된 작업이 없습니다.</p>}{rows.map(row => <p key={row.job_id} className="mt-1 break-all">{row.task||'작업'} · GPU {row.selector} {row.allow_sharing?`· ${row.memory_budget_mb} MiB 공유`:''} · {row.requires_reconciliation ? '원격 상태 재확인 필요 — 예약 유지' : '예약 중'}</p>)}{error && <p role="alert" className="mt-1 text-red-300">{error}</p>}</div>;
};
