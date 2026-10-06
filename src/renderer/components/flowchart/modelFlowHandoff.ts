import type {FlowchartPipeline,FlowNode,FlowNodeData} from '../../types';
import type {FlowModelCatalogItem} from '../../services/api';
import {taskHandoffScope,type HandoffScope} from '../training/taskHandoff';
type StorageLike=Pick<Storage,'getItem'|'setItem'|'removeItem'>;
export type ModelFlowHandoff={scope:string;modelId:string;family:string;datasetPath:string;selectedAt:number};
const key=(scope:string)=>`vision-model-flow:${scope}`;
export function saveModelFlowHandoff(storage:StorageLike,state:HandoffScope,family:string,modelId:string,datasetPath=''){
 if(!state.projectDir||!state.project?.source_dataset_dir||!modelId)throw new Error('프로젝트와 완료 모델을 먼저 선택하세요.');
 if(family==='defect_gan')throw new Error('생성 모델은 후보 생성·검토·채택 화면에서 사용하세요.');
 const scope=taskHandoffScope(state),row={scope,modelId,family,datasetPath,selectedAt:Date.now()};storage.setItem(key(scope),JSON.stringify(row));return row;
}
export function readModelFlowHandoff(storage:StorageLike,state:HandoffScope):ModelFlowHandoff|null{
 const scope=taskHandoffScope(state),value=storage.getItem(key(scope));if(!value)return null;
 try{const row=JSON.parse(value);if(row.scope===scope&&typeof row.modelId==='string'&&row.modelId&&typeof row.family==='string')return row;}catch{}return null;
}
export function clearModelFlowHandoff(storage:StorageLike,state:HandoffScope){storage.removeItem(key(taskHandoffScope(state)));}
export function compatibleModelNode(node:FlowNode,model:Pick<FlowModelCatalogItem,'task'|'capabilities'>){
 if(model.capabilities?.role==='preprocess')return node.data.node_type==='preprocess'&&node.data.params?.operation===model.capabilities.operation;
 if(node.data.node_type==='detection_crop'&&['detection','rotated_detection'].includes(model.task))return (node.data.task||'detection')===model.task;
 return node.data.node_type==='inspection'&&node.data.task===model.task;
}
export function modelScoreBinding(model:FlowModelCatalogItem):Partial<FlowNodeData>{
 const spec=model.score_spec;
 return {model_job_id:model.job_id,threshold:spec?.threshold??model.threshold_settings?.threshold??.5,score_spec:spec?{...spec}:undefined};
}
export function thresholdUpdate(data:Partial<FlowNodeData>,threshold:number):Partial<FlowNodeData>{
 if(!Number.isFinite(threshold)||threshold<0||(data.score_spec?.domain!=='distance'&&threshold>1))throw new Error('임계값 threshold 범위를 확인하세요.');
 return {threshold,...(data.score_spec?{score_spec:{...data.score_spec,threshold}}:{})};
}
export function bindModelToFlow(pipeline:FlowchartPipeline,model:FlowModelCatalogItem,nodeId?:string){
 if(String(model.task)==='defect_gan')throw new Error('생성 모델은 검사 노드에 연결하지 않습니다.');
 if(nodeId){const node=pipeline.nodes.find(row=>row.id===nodeId);if(!node||!compatibleModelNode(node,model))throw new Error('선택 모델과 호환되는 노드를 선택하세요.');
   return {nodeId,pipeline:{...pipeline,nodes:pipeline.nodes.map(row=>row.id===nodeId?{...row,data:{...row.data,...modelScoreBinding(model)}}:row)}};
 }
 if(pipeline.nodes.filter(row=>['inspection','detection_crop','preprocess'].includes(row.data.node_type)).length>=8)throw new Error('모델 노드 제한에 도달했습니다. 기존 호환 노드를 선택하세요.');
 const id=`model_${Date.now()}_${Math.random().toString(36).slice(2,8)}`;
 const node:FlowNode={id,position:{x:640,y:80+pipeline.nodes.length*80},data:{label:model.label||model.task,
   node_type:model.capabilities?.role==='preprocess'?'preprocess':'inspection',model_job_id:model.job_id,
   ...(model.capabilities?.role==='preprocess'?{params:{operation:model.capabilities.operation}}:{task:model.task,...modelScoreBinding(model),params:{}})}};
 return {nodeId:id,pipeline:{...pipeline,nodes:[...pipeline.nodes,node]}};
}
