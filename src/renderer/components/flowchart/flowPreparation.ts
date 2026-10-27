import type {FlowchartPipeline} from '../../types';

type PreparationAction = 'wait'|'review-model'|'edit'|'save'|'evaluate'|'inspect';
export interface FlowPreparationState {
  loading:boolean; busy:boolean; hasPipeline:boolean; modelBlocked:boolean; needsModel:boolean;
  graphError:string|null; canSave:boolean; exactSaved:boolean; evaluationCurrent:boolean;
}

/** A valid receipt can still contain zero reviewed labels. Match the evaluation panel's human-truth prerequisite. */
export function hasReviewedFlowEvaluation(identityCurrent:boolean,knownTruthCount:number):boolean {
  return identityCurrent&&Number.isSafeInteger(knownTruthCount)&&knownTruthCount>0;
}

/** Presentation policy only. Each effect still goes through its existing source and saved-evidence guards. */
export function flowPreparationNext(state:FlowPreparationState):{action:PreparationAction;label:string;disabled:boolean} {
  if(state.loading||state.busy)return {action:'wait',label:'현재 작업 확인 중…',disabled:true};
  if(!state.hasPipeline||state.modelBlocked)return {action:'review-model',label:'학습한 모델 확인하기',disabled:false};
  if(state.needsModel)return {action:'edit',label:'검사 모델 연결하기',disabled:false};
  if(state.graphError)return {action:'edit',label:'검사 흐름의 설정 확인하기',disabled:false};
  if(!state.exactSaved)return {action:'save',label:'이 검사 흐름 저장하기',disabled:!state.canSave};
  if(!state.evaluationCurrent)return {action:'evaluate',label:'시험 이미지로 검사 확인하기',disabled:false};
  return {action:'inspect',label:'6단계 검사 실행으로 이동',disabled:false};
}

const taskNames:Record<string,string>={classification:'분류',segmentation:'영역 분할',detection:'객체 검출',
  anomaly:'이상 탐지',patch_classification:'패치 분류',rotated_detection:'회전 객체 검출',ocr:'문자 인식',rotation:'회전 보정',enhancement:'학습형 영상 개선'};
const ruleNames:Record<string,string>={any_ng:'하나라도 NG',all_ng:'모두 NG',any_defect_is_ng:'결함 하나라도 NG',
  score_gt_threshold:'최고 점수 임계치',max_flaws_allowed:'허용 NG 검사 영역 수',aggregate_verdict:'집계 결과 판정',patch_recipe:'저장된 패치 판정 설정'};

/** A read-only summary: custom and multi-model flows are never reduced to a single-model recipe. */
export function flowPreparationSummary(pipeline:FlowchartPipeline|null) {
  const nodes=pipeline?.nodes||[];
  return {
    models:nodes.filter(node=>node.data.node_type==='inspection'||node.data.node_type==='detection_crop'
      ||node.data.node_type==='preprocess'&&['learned_rotation','enhancement'].includes(node.data.params?.operation))
      .map(node=>({label:node.data.label,task:taskNames[node.data.node_type==='preprocess'
        ?node.data.params?.operation==='enhancement'?'enhancement':'rotation':node.data.task||'']||'검사 모델',jobId:node.data.model_job_id||undefined})),
    rules:nodes.filter(node=>node.data.node_type==='decision'||node.data.node_type==='aggregate')
      .map(node=>{
        const data=node.data,rule=ruleNames[data.rule||'']||'고급 편집에서 세부 규칙 확인';
        const bound=data.rule==='score_gt_threshold'?` · 기준 점수 ${data.threshold??0.5} · ${data.score_spec?.unit||'점수 단위는 고급 편집에서 확인'}`
          :data.rule==='max_flaws_allowed'?` · 최대 ${data.params?.max_flaws_allowed??0}개 허용`
          :data.rule==='patch_recipe'?' · 패치별 수치 설정은 고급 편집에서 확인':'';
        return `${data.label} · ${rule}${bound}`;
      }),
  };
}
