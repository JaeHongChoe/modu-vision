import {ImageRoiEditor} from './ImageRoiEditor';
import {useEffect,useState} from 'react';
import {geometryFlowApi,type FixtureRecord} from '../../services/geometryFlowApi';

export function FixtureReferencePanel({params,onChange,imagePath}:{params:Record<string,any>;onChange:(params:Record<string,any>)=>void;imagePath?:string}) {
  const [records,setRecords]=useState<FixtureRecord[]>([]);
  const [error,setError]=useState('');
  const [preview,setPreview]=useState<{ref:string;url:string;size:number[]}|null>(null);
  const fixture=params.fixture;
  const reload=()=>geometryFlowApi.fixtures().then(result=>setRecords(result.fixtures)).catch(err=>setError(String(err)));
  useEffect(()=>{void reload();},[]);
  useEffect(()=>{let active=true;setPreview(null);if(fixture?.reference_ref)void geometryFlowApi.fixturePreview(fixture.reference_ref).then(result=>{if(active)setPreview({ref:result.reference_ref,url:result.preview_data_url,size:result.source_size});}).catch(err=>{if(active)setError(String(err));});return()=>{active=false;};},[fixture?.reference_ref]);
  const update=(value:Record<string,any>)=>onChange({...params,fixture:{...fixture,...value}});
  return <section aria-label="기준물 정렬" className="space-y-2 text-xs">
    <label>기준물 정렬<select aria-label="기준물 참조" value={fixture?.reference_ref||''} onChange={event=>{
      const record=records.find(row=>row.ref===event.target.value);
      if(record)update({reference_ref:record.ref,reference_revision:record.revision,scope:fixture?.scope||'rigid',min_support:fixture?.min_support??.8});
      else {const next={...params};delete next.fixture;onChange(next);}
    }}><option value="">원본의 고정 좌표</option>
      {fixture&&!records.some(row=>row.ref===fixture.reference_ref)&&<option value={fixture.reference_ref}>변경되거나 없는 참조 · 실행 시 REVIEW</option>}
      {records.map(row=><option key={row.ref} value={row.ref}>{row.name} · r{row.revision} · {row.ref.slice(-12)}</option>)}
    </select></label>
    <button type="button" disabled={!imagePath} onClick={async()=>{
      try {setError('');if(!imagePath)return;const source=await geometryFlowApi.source(imagePath);
        const record=await geometryFlowApi.createFixture({image_path:imagePath,name:'기준물',valid_region:[0,0,...source.source_size]});
        setRecords(rows=>[...rows,record]);update({reference_ref:record.ref,reference_revision:record.revision,scope:'rigid',min_support:.8});
      }catch(err){setError(String(err));}
    }}>현재 이미지로 기준물 저장</button>
    {fixture&&<>
      {preview&&preview.ref===fixture.reference_ref&&<ImageRoiEditor preview={preview.url} sourceSize={preview.size} roi={params.roi_bbox||[0,0,512,512]} onChange={roi=>onChange({...params,roi_bbox:roi})}/>}
      <label>변환 범위<select aria-label="기준물 변환 범위" value={fixture.scope} onChange={event=>update({scope:event.target.value})}><option value="rigid">이동·회전</option><option value="similarity">이동·회전·지원 배율</option></select></label>
      <label>최소 ROI 지지율<input aria-label="최소 ROI 지지율" type="number" min="0" max="1" step=".05" value={fixture.min_support??.8} onChange={event=>{const value=event.target.valueAsNumber;if(Number.isFinite(value)&&value>=0&&value<=1)update({min_support:value});}}/></label>
      <p>ROI 좌표는 저장한 기준 이미지의 좌표입니다. 참조 변경·정렬 실패·모호한 위치는 REVIEW로 기록하고 모델 검사를 중단합니다.</p>
      <details><summary>정렬 품질 한계</summary>{(['max_residual_px','max_placement_px','min_similarity_scale','max_similarity_scale','max_ambiguity','min_relative_detail'] as const).map(key=><label key={key}>{{"max_residual_px": "최대 잔차 (px)", "max_placement_px": "최대 위치 오차 (px)", "min_similarity_scale": "최소 지원 배율", "max_similarity_scale": "최대 지원 배율", "max_ambiguity": "최대 위치 모호도", "min_relative_detail": "최소 세부 정보 유지율"}[key]}<input aria-label={key} type="number" step="any" value={fixture.limits?.[key]??({max_residual_px:2,max_placement_px:3,min_similarity_scale:.7,max_similarity_scale:1.4,max_ambiguity:.3,min_relative_detail:.1}[key])} onChange={event=>{const value=event.target.valueAsNumber;if(Number.isFinite(value)&&value>0)update({limits:{...fixture.limits,[key]:value}});}}/></label>)}</details>
    </>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
