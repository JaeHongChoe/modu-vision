import type {FlowchartPipeline, FlowNode, FlowModelTask, FlowEdge} from '../../types';
import type {FlowModelCatalogItem} from '../../services/api';
import {modelScoreBinding} from './modelFlowHandoff';
import {validateFlowchartGraph} from './flowchartGraph';

export type FlowRecipeKind = 'single' | 'detector' | 'obb' | 'fixed' | 'rotation' | 'multi';
export const FLOW_RECIPES: Array<{id:FlowRecipeKind; title:string; description:string}> = [
  {id:'single',title:'단일 모델 검사',description:'원본 이미지 → 모델 → 최종 판정'},
  {id:'detector',title:'검출 ROI 검사',description:'검출 모델의 ROI → 검사 모델 → 최종 판정'},
  {id:'obb',title:'회전 ROI 정렬 검사',description:'회전 검출 다각형 → 맞춤 정렬 → 검사 모델'},
  {id:'fixed',title:'고정 ROI 검사',description:'원본 픽셀 좌표 ROI → 검사 모델 → 최종 판정'},
  {id:'rotation',title:'학습 회전 → OCR·검사',description:'학습된 회전 보정 → OCR 또는 검사 모델'},
  {id:'multi',title:'전처리 → 다중 모델 → 집계',description:'영상 개선 → 두 모델의 독립 판정 → 하나라도 NG'},
];
/** Tasks an inspection node runs; detection models only feed ROI (detection_crop) nodes. */
export const INSPECTION_TASKS: FlowModelTask[] = ['classification','segmentation','anomaly','patch_classification','ocr','rotated_detection'];

export function recipeModelNodes(pipeline:FlowchartPipeline) {
  return pipeline.nodes.filter(node=>['inspection','detection_crop'].includes(node.data.node_type) ||
    (node.data.node_type==='preprocess'&&node.data.params?.operation==='learned_rotation'));
}

/** The tasks this recipe node can run, independent of which models exist. */
export function recipeNodeTasks(node:FlowNode):FlowModelTask[] {
  if(node.data.node_type==='detection_crop')return ['detection','rotated_detection'];
  if(node.data.node_type==='preprocess')return ['rotation'];
  return INSPECTION_TASKS;
}

/** Completed models a node can be mapped to: same task, and a task the node runs. Adoption still verifies them. */
export function selectableModels(node:FlowNode, catalog:FlowModelCatalogItem[]):FlowModelCatalogItem[] {
  return catalog.filter(model=>model.task===node.data.task&&recipeNodeTasks(node).includes(model.task as FlowModelTask));
}

/** A detection project inspects ROIs with a segmentation model, as the existing starters did. */
export function recipeInspectionTask(projectTask:FlowModelTask):FlowModelTask {
  return INSPECTION_TASKS.includes(projectTask)?projectTask:'segmentation';
}

export function createFlowRecipe(kind:FlowRecipeKind, projectTask:FlowModelTask):FlowchartPipeline {
  const nodes:FlowNode[]=[], edges:FlowEdge[]=[];
  const node=(id:string,data:FlowNode['data'],y=160)=>{nodes.push({id,position:{x:40+nodes.length*285,y},data});return id;};
  const edge=(source:string,target:string,payload_type:FlowEdge['payload_type'])=>edges.push({id:`${source}-${target}`,source,target,payload_type});
  const finish=(inputs:string[],rule:string)=>{
    const decision=node('node_decision',{label:'최종 판정',node_type:'decision',rule});
    for(const source of inputs)edge(source,decision,'result');
    const output=node('node_output',{label:'검사 결과',node_type:'output'});edge(decision,output,'result');
    return {id:`recipe_${kind}`,name:FLOW_RECIPES.find(row=>row.id===kind)!.title,description:'모델과 클래스 적용 범위를 확인한 새 초안',nodes,edges};
  };
  let upstream=node('node_input',{label:'검사 이미지',node_type:'input'});
  if(kind==='single'&&projectTask==='detection'){
    // A detection project's single-model flow is the detector itself, as the existing single-detection template.
    const crop=node('node_crop',{label:'결함 객체 검출',node_type:'detection_crop',task:'detection',crop_padding:0,params:{}});
    edge(upstream,crop,'image');
    return finish([crop],'any_defect_is_ng');
  }
  const task=recipeInspectionTask(projectTask);
  if(kind==='fixed'){const id=node('node_fixed_roi',{label:'고정 ROI',node_type:'fixed_roi',params:{roi_bbox:[0,0,512,512]}});edge(upstream,id,'image');upstream=id;}
  if(kind==='detector'){const id=node('node_crop',{label:'관심 영역 검출',node_type:'detection_crop',task:'detection',crop_padding:12,params:{}});edge(upstream,id,'image');upstream=id;}
  if(kind==='obb'){
    const id=node('node_crop',{label:'회전 영역 검출',node_type:'detection_crop',task:'rotated_detection',crop_padding:0,params:{}});edge(upstream,id,'image');
    const aligned=node('node_align',{label:'회전 ROI 맞춤 정렬',node_type:'preprocess',params:{operation:'fitted_roi'}});edge(id,aligned,'roi');upstream=aligned;
  }
  if(kind==='rotation'){const id=node('node_rotate',{label:'학습 회전 보정',node_type:'preprocess',task:'rotation',params:{operation:'learned_rotation'}});edge(upstream,id,'image');upstream=id;}
  if(kind==='multi'){const id=node('node_preprocess',{label:'영상 개선',node_type:'preprocess',params:{operation:'improve',contrast:1.1,brightness:0}});edge(upstream,id,'image');upstream=id;}
  const roles=kind==='multi'?['node_inspect','node_inspect_2']:['node_inspect'];
  for(const [index,id] of roles.entries()){
    node(id,{label:kind==='multi'?`검사 모델 ${index+1}`:'검사 모델',node_type:'inspection',task,params:{}},160+index*240);
    edge(upstream,id,kind==='single'?'image':'roi');
  }
  if(kind==='multi'){const id=node('node_aggregate',{label:'독립 판정 집계',node_type:'aggregate',rule:'any_ng'});for(const source of roles)edge(source,id,'result');return finish([id],'aggregate_verdict');}
  return finish(roles,'any_defect_is_ng');
}

export interface OcrExpectation { kind:'expected_text'|'regex'; value:string }

/** All classes is explicit; recipes introduce no guessed numeric class rules. An OCR node needs its expectation.
 *  The mapped draft must pass the graph validator, so adoption never installs a draft that cannot run. */
export function mapFlowRecipe(preview:FlowchartPipeline, catalog:FlowModelCatalogItem[], mapping:Record<string,string>,
  policies:Record<string,string>, expectations:Record<string,OcrExpectation>={}):FlowchartPipeline {
  const mapped=new Map<string,FlowNode>();
  for(const node of recipeModelNodes(preview)){
    const model=catalog.find(row=>row.job_id===mapping[node.id]);
    if(!model)throw new Error(`${node.data.label}: 완료 모델을 선택하세요.`);
    if(!selectableModels(node,catalog).includes(model))throw new Error(`${node.data.label}: 이 노드가 실행할 수 있는 작업의 모델을 선택하세요.`);
    if(policies[node.id]!=='all')throw new Error(`${node.data.label}: 클래스 적용 범위를 명시적으로 선택하세요.`);
    let params={...(node.data.params||{})};
    if(node.data.task==='ocr'){
      const expectation=expectations[node.id];
      if(!expectation?.value.trim())throw new Error(`${node.data.label}: OCR 기대 문자열 또는 정규식을 입력하세요.`);
      // The editor checks the pattern with JavaScript and the backend runs it with Python; their named-group syntaxes
      // differ ((?<n>…) vs (?P<n>…)), so named groups and their references are refused rather than failing later.
      if(expectation.kind==='regex'&&/\(\?P?<(?![=!])|\\k<|\(\?P[=>]/.test(expectation.value))throw new Error(`${node.data.label}: 이름 있는 그룹은 쓸 수 없습니다. 편집기와 서버의 정규식 문법이 다릅니다.`);
      // Forms only one side accepts: \c and \p{…} escapes, the empty negated class [^], variable-width lookbehind.
      if(expectation.kind==='regex'&&(/\\c|\\[pP]\{|\[\^\]/.test(expectation.value)||/\(\?<[=!][^)]*([*+?]|\{\d*,)/.test(expectation.value)))
        throw new Error(`${node.data.label}: 서버(Python)가 지원하지 않는 정규식 표현입니다.`);
      params={...params,[expectation.kind]:expectation.value};
    }
    mapped.set(node.id,{...node,data:{...node.data,params,...modelScoreBinding(model)}});
  }
  const draft={...preview,nodes:preview.nodes.map(node=>mapped.get(node.id)||{...node,data:{...node.data}}),edges:preview.edges.map(edge=>({...edge}))};
  const issue=validateFlowchartGraph(draft);
  if(issue)throw new Error(`채택할 수 없는 초안입니다: ${issue}`);
  return draft;
}

/** The ports the recipe connects, as the user reads them: source → target (payload). */
export function recipePorts(preview:FlowchartPipeline):string[] {
  const label=(id:string)=>preview.nodes.find(node=>node.id===id)?.data.label||id;
  return preview.edges.map(edge=>`${label(edge.source)} → ${label(edge.target)} (${edge.payload_type||'result'})`);
}
