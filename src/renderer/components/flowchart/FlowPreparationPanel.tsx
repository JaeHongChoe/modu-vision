import type {FlowWorkspaceTab} from './FlowEditorWorkspace';

export interface FlowPreparationPanelProps {
  area: FlowWorkspaceTab;
  models: Array<{label:string; task:string; jobId?:string}>;
  rules: string[];
  exactSaved: boolean;
  evaluationCurrent: boolean;
  busy: boolean;
  loading: boolean;
  problem?: string;
  next: {label:string; disabled:boolean; onClick:()=>void};
  onAdvanced: ()=>void;
  onPrepare: ()=>void;
  onEvaluate: ()=>void;
  onReviewModel: ()=>void;
  onBasicRecipe?: ()=>void;
}

/** A view of the existing graph and receipts, with no execution or persistence owner of its own. */
export function FlowPreparationPanel(props:FlowPreparationPanelProps) {
  const {area,models,rules,exactSaved,busy,loading,problem,next} = props;
  const editing = area === 'edit';
  const modelReady = models.length > 0 && models.every(model=>Boolean(model.jobId?.trim()));
  const rulesReady = rules.length > 0;
  // Even a retained evaluation prop cannot qualify an edited/unsaved graph.
  const evaluationReady = modelReady && rulesReady && exactSaved && props.evaluationCurrent;
  const current = !modelReady ? 0 : !rulesReady ? 1 : !exactSaved ? 2 : !evaluationReady ? 3 : 4;
  const locked = busy || loading;
  const guarded = (callback:()=>void,disabled=false)=>()=>{
    if(!props.busy&&!props.loading&&!disabled)callback();
  };
  const stages = [
    {id:'model',title:'사용할 모델',status:modelReady?'모델 연결됨':'모델 연결 필요'},
    {id:'rule',title:'판정 기준',status:rulesReady?'판정 기준 있음':'판정 기준 필요'},
    {id:'save',title:'흐름 저장',status:exactSaved?'저장됨':'저장 필요'},
    {id:'evaluate',title:'시험 이미지 확인',status:evaluationReady?'평가 근거 확인됨':'평가 근거 확인 필요'},
    {id:'inspect',title:'검사 실행',status:'6단계에서 진행'},
  ];
  return <section aria-label="검사 준비" tabIndex={-1} aria-busy={locked}
    className="min-w-0 shrink-0 space-y-4 p-4 text-sm text-[var(--workspace-text)]">
    <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 space-y-1">
        <h2 className="text-base font-semibold">{editing?'검사 준비':area==='evaluate'?'시험 이미지로 검사 확인':area==='test'?'선택 이미지 시험':'실행 전 확인'}</h2>
        <p className="text-[var(--workspace-muted)]">4단계에서 확인한 모델에 판정 기준을 더해 실제 검사 순서를 준비합니다.</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {!editing&&<button type="button" className="workspace-button" disabled={locked}
          onClick={guarded(props.onPrepare)}>검사 준비로 돌아가기</button>}
        <button type="button" className="workspace-button" disabled={locked}
          onClick={guarded(props.onAdvanced)}>고급 편집</button>
      </div>
    </div>

    <ol aria-label="검사 준비 순서" className={editing?'grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5':'flex min-w-0 flex-wrap gap-2'}>
      {stages.map((stage,index)=><li key={stage.id} data-preparation-stage={stage.id}
        aria-current={index===current?'step':undefined}
        className={editing?'workspace-record space-y-2':'min-w-0 rounded border border-[var(--workspace-border)] px-3 py-2'}
        style={index===current?{borderColor:'var(--workspace-accent)'}:undefined}>
        <p className="font-semibold">{index+1}. {stage.title}</p>
        <p className="text-[13px] text-[var(--workspace-muted)]">{stage.status}</p>
        {editing&&stage.id==='model'&&<>
          {models.length?<ul className="space-y-2 text-[13px]">{models.map((model,i)=><li key={i} className="min-w-0 break-words">
            <span className="font-semibold">{model.label}</span><span className="block text-[var(--workspace-muted)]">{model.task}{!model.jobId&&' · 모델 미선택'}</span>
          </li>)}</ul>:<p className="text-[13px] text-[var(--workspace-muted)]">학습이 끝난 모델을 연결하세요.</p>}
          {models.some(model=>model.jobId)&&<details className="min-w-0 text-[13px] text-[var(--workspace-muted)]">
            <summary className="cursor-pointer">모델 작업 기록</summary>
            <ul className="mt-2 min-w-0 space-y-2">{models.filter(model=>model.jobId).map((model,i)=><li key={i} className="min-w-0 break-all">
              {model.label} · {model.jobId}
            </li>)}</ul>
          </details>}
          {!modelReady&&props.onBasicRecipe&&<>
            <button type="button" className="workspace-button" disabled={locked}
              onClick={guarded(props.onBasicRecipe)}>기본 검사 흐름 구성</button>
            <p className="text-[13px] text-[var(--workspace-muted)]">미리보기를 열고 모델을 선택합니다. 기존 흐름은 확인한 뒤에만 바뀝니다.</p>
          </>}
          <button type="button" className="workspace-button" disabled={locked}
            onClick={guarded(props.onReviewModel)}>모델 다시 선택</button>
        </>}
        {editing&&stage.id==='rule'&&<>
          {rules.length?<ul className="space-y-1 text-[13px]">{rules.map((rule,i)=><li key={i} className="break-words">{rule}</li>)}</ul>
            :<p className="text-[13px] text-[var(--workspace-muted)]">어떤 결과를 정상·불량으로 볼지 확인하세요.</p>}
          <button type="button" className="workspace-button" disabled={locked}
            onClick={guarded(props.onAdvanced)}>판정 기준 수정</button>
        </>}
        {editing&&stage.id==='save'&&<p className="text-[13px] text-[var(--workspace-muted)]">현재 모델과 판정 기준을 함께 저장합니다. 변경 후에는 다시 저장하세요.</p>}
        {editing&&stage.id==='evaluate'&&<>
          <p className="text-[13px] text-[var(--workspace-muted)]">저장한 검사 흐름 전체를 시험 이미지로 확인합니다.</p>
          {exactSaved&&<button type="button" className="workspace-button" disabled={locked}
            onClick={guarded(props.onEvaluate)}>평가 화면 열기</button>}
        </>}
        {editing&&stage.id==='inspect'&&<p className="text-[13px] text-[var(--workspace-muted)]">6단계에서 검사 이미지와 실행 준비 상태를 확인합니다.</p>}
      </li>)}
    </ol>

    <div className="min-w-0 space-y-2">
      <p role="status" className="min-h-5 break-words text-[var(--workspace-muted)]">
        {loading?'현재 프로젝트의 검사 흐름을 확인하는 중입니다.':busy?'진행 중인 작업이 끝난 뒤 계속하세요.':problem||
          (evaluationReady?'현재 저장 버전의 평가 근거를 확인했습니다. 최종 승인 상태는 6단계에서 확인하세요.':'위 순서대로 준비한 뒤 다음 작업을 진행하세요.')}
      </p>
      {editing&&<button type="button" data-primary-action="true" className="workspace-button workspace-button--primary"
        disabled={locked||next.disabled} onClick={guarded(next.onClick,next.disabled)}>{next.label}</button>}
      <p className="text-[13px] text-[var(--workspace-muted)]">정답 확정·최종 승인·배포는 사람이 직접 결정합니다. 화면 이동이나 저장만으로 승인되지 않습니다.</p>
    </div>
  </section>;
}
