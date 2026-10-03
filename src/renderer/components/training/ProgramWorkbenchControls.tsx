export {openModelFlow} from './openModelFlow';
import type { ReactNode } from 'react';
import { type LocalTrainingDevice } from '../../services/modelTrainingProgram';
import {useComputeStore} from '../../stores/useComputeStore';

export const programInput = 'mt-1 w-full rounded border border-[#344255] bg-[#0B1520] px-3 py-2 text-slate-100 disabled:opacity-40';
export const programButton = 'rounded border border-[#344255] px-3 py-2 text-slate-200 hover:bg-[#25344A] disabled:opacity-40';
export const programPrimary = 'rounded bg-cyan-700 px-3 py-2 font-semibold text-white hover:bg-cyan-600 disabled:opacity-40';
export function ProgramField({label, children}: {label: string; children: ReactNode}) {
  return <label className="block text-xs text-slate-300">{label}{children}</label>;
}
export function TrainingDeviceSelector({value, onChange, disabled = false,localOnly=false}: {value: LocalTrainingDevice; onChange: (value: LocalTrainingDevice) => void; disabled?: boolean;localOnly?:boolean}) {
  const compute=useComputeStore();
  return <div><ProgramField label="학습 실행 위치"><select aria-label="모델 학습 실행 위치" value={compute.selectedProfileId||''} disabled={disabled||compute.isLoading} onChange={event=>void compute.selectTarget(event.target.value||null)} className={programInput}><option value="">이 컴퓨터</option>{localOnly&&compute.selectedProfileId&&<option value={compute.selectedProfileId}>선택 서버 · 로컬로 변경 필요</option>}{!localOnly&&compute.profiles.map(profile=><option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></ProgramField>{compute.error&&<p role="alert" className="mt-1 text-rose-300">{compute.error}</p>}<ProgramField label="로컬 학습·평가 장치"><select aria-label="학습 평가 장치" value={value} disabled={disabled} onChange={event => onChange(event.target.value as LocalTrainingDevice)} className={programInput}>
    <option value="cpu">CPU</option><option value="mps">Apple Metal / MPS</option><option value="cuda">CUDA GPU</option>
  </select></ProgramField>{localOnly&&<p className="mt-1 text-slate-400">자동 후보 탐색은 이 컴퓨터에서 실행합니다. 서버 학습은 위 작업대에서 설정하세요.</p>}{!localOnly&&compute.selectedProfileId&&<p className="mt-1 text-slate-400">학습은 선택 서버에서 실행합니다. 저장 모델의 로컬 시험 평가는 위 장치를 사용합니다.</p>}</div>;
}
