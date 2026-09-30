import React, { useEffect, useState } from 'react';
import { request } from '../../services/api';
type Lease = { job_id: string; host: string; selector: string; expires: number; uncertain: number; requires_reconciliation: boolean };
export const ComputeReservations: React.FC = () => {
  const [rows, setRows] = useState<Lease[]>([]); const [error, setError] = useState('');
  useEffect(() => {
    let valid = true;
    const refresh = () => request<{ reservations: Lease[] }>('/api/compute/reservations').then(result => { if (valid) { setRows(result.reservations); setError(''); } }).catch(cause => { if (valid) setError(cause.message || String(cause)); });
    void refresh(); const timer = window.setInterval(refresh, 5000);
    return () => { valid = false; window.clearInterval(timer); };
  }, []);
  return <div className="mt-3 rounded border border-slate-700 p-3 text-xs"><h3 className="font-bold">공유 자원 예약</h3>{rows.length === 0 && <p className="mt-1 text-slate-400">예약된 작업이 없습니다.</p>}{rows.map(row => <p key={row.job_id} className="mt-1 break-all">{row.job_id} · {row.host} · GPU {row.selector} · {row.requires_reconciliation ? '원격 상태 재확인 필요 — 예약 유지' : '예약 중'}</p>)}{error && <p role="alert" className="mt-1 text-red-300">{error}</p>}</div>;
};
