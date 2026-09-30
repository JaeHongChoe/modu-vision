import React from 'react';
import type {SizeControls} from '../../services/foundationLabelingApi';
export type LabelAssistExecution=SizeControls&{device:string};
export const LabelAssistDeviceSizes:React.FC<{value:LabelAssistExecution;onChange:(value:LabelAssistExecution)=>void;disabled:boolean}>=({value,onChange,disabled})=>
  <details><summary className="cursor-pointer text-cyan-200">실행 장치·원본 객체 크기 필터</summary><div className="mt-2 grid grid-cols-2 gap-2">
    <label>장치<select aria-label="완료 모델 라벨링 실행 장치" value={value.device} onChange={e=>onChange({...value,device:e.target.value})} disabled={disabled} className="mt-1 w-full rounded border border-slate-600 bg-slate-900 p-1.5">{['cpu','auto','mps','cuda:0','cuda:1'].map(d=><option key={d}>{d}</option>)}</select></label>
    {([['min_area','최소 면적'],['max_area','최대 면적'],['min_width','최소 너비'],['max_width','최대 너비'],['min_height','최소 높이'],['max_height','최대 높이']] as const).map(([key,label])=><label key={key}>{label} (px)<input aria-label={`완료 모델 ${label}`} disabled={disabled} type="number" min={0} value={value[key]??''} placeholder="제한 없음" onChange={e=>onChange({...value,[key]:e.target.value===''?undefined:Number(e.target.value)})} className="mt-1 w-full rounded border border-slate-600 bg-slate-900 p-1.5"/></label>)}
  </div></details>;
