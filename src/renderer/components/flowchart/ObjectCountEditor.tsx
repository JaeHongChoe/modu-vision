type Rule = { class_name: string; min_count: number; max_count?: number };
export function ObjectCountEditor({params,classes,onChange}:{params:Record<string,any>;classes:string[];onChange:(params:Record<string,any>)=>void}) {
  const rules:Rule[]|undefined=params.object_requirements;
  const set=(next:Rule[]|undefined)=>{const copy={...params};if(next)copy.object_requirements=next;else delete copy.object_requirements;onChange(copy);};
  return <fieldset className="space-y-2 rounded border border-sky-800 p-3"><legend>검출 객체 수 규칙</legend>
    <label>검출 판정 방식<select aria-label="검출 판정 방식" value={rules?'count':'defect'} onChange={e=>set(e.target.value==='count'?[{class_name:classes[0]||'',min_count:1}]:undefined)} className="ml-2 rounded bg-slate-800 p-1"><option value="defect">검출된 결함은 NG</option><option value="count" disabled={!classes.length}>필수 객체·개수 검사</option></select></label>
    {rules&&<><p className="text-xs text-slate-400">완료된 학습 모델의 신뢰도 필터를 통과한 박스를 셉니다. 겹친 입력 ROI에서 검출된 박스는 각각 집계됩니다. 판정 노드에 조건 없이 직접 연결하고 ‘하나라도 결함이면 NG’를 사용하세요.</p>
      {rules.map((rule,index)=><div key={index} className="space-y-1">
        <label>객체 클래스<select aria-label={`객체 클래스 ${index+1}`} value={rule.class_name} onChange={e=>set(rules.map((r,i)=>i===index?{...r,class_name:e.target.value}:r))} className="ml-2 rounded bg-slate-800 p-1">{!classes.includes(rule.class_name)&&<option value={rule.class_name}>{rule.class_name||'모델 클래스 선택 필요'}</option>}{classes.map(name=><option key={name}>{name}</option>)}</select></label>
        <label className="block">최소 개수<input aria-label={`객체 최소 개수 ${index+1}`} type="number" min="0" max="1000000" value={rule.min_count} onChange={e=>set(rules.map((r,i)=>i===index?{...r,min_count:Number(e.target.value)}:r))} className="ml-2 w-20 rounded bg-slate-800 p-1"/></label>
        <label className="block">최대 개수 (비우면 제한 없음)<input aria-label={`객체 최대 개수 ${index+1}`} type="number" min={rule.min_count} max="1000000" value={rule.max_count??''} onChange={e=>set(rules.map((r,i)=>i===index?{...r,max_count:e.target.value===''?undefined:Number(e.target.value)}:r))} className="ml-2 w-20 rounded bg-slate-800 p-1"/></label>
        <button type="button" disabled={rules.length===1} onClick={()=>set(rules.filter((_,i)=>i!==index))}>객체 규칙 {index+1} 삭제</button>
      </div>)}
      <button type="button" disabled={rules.length>=64||classes.every(name=>rules.some(r=>r.class_name===name))} onClick={()=>set([...rules,{class_name:classes.find(name=>!rules.some(r=>r.class_name===name))||'',min_count:1}])}>객체 클래스 규칙 추가</button>
    </>}
  </fieldset>;
}
