import type { ActionId, TaskPlanInput } from './types';
import {validateInputRecipe,type FixedInputRecipe} from '../../components/training/inputPreparation';

export class TaskValidationError extends Error {
  readonly code = 'invalid_task_plan';
  constructor(message: string) { super(message); this.name = 'TaskValidationError'; }
}
type Schema = Record<string, unknown>;
export interface TaskActionDefinition {
  id: ActionId;
  title: string;
  description: string;
  changesBinding: boolean;
  inputSchema: Schema;
}
const string = (maxLength: number, extra: Schema = {}): Schema => ({type: 'string', minLength: 1, maxLength, ...extra});
const enumeration = (values: string[]): Schema => ({type: 'string', enum: values});
const object = (properties: Schema = {}, required: string[] = []): Schema => ({type: 'object', properties, required, additionalProperties: false});
const job = string(256);
const percent = {type: 'number', minimum: 0, maximum: 100};
const recipeSchema:Schema=object({version:{type:'integer',minimum:1,maximum:1},operations:{type:'array',maxItems:16,items:{oneOf:[
  object({op:enumeration(['crop']),roi:{type:'array',minItems:4,maxItems:4,items:{type:'number',minimum:0,maximum:1}}},['op','roi']),
  object({op:enumeration(['rotate90']),turns:{type:'integer',minimum:1,maximum:3}},['op','turns']),
  object({op:enumeration(['brightness','contrast']),factor:{type:'number',minimum:.25,maximum:4}},['op','factor']),
  object({op:enumeration(['resize']),width:{type:'integer',minimum:16,maximum:4096},height:{type:'integer',minimum:16,maximum:4096}},['op','width','height']),
]}}},['version','operations']);
export const ACTION_CATALOG: readonly TaskActionDefinition[] = [
  {id:'create_project', title:'프로젝트 만들기', description:'이 컴퓨터에서 저장 위치를 고르고 새 검사 프로젝트를 만듭니다.', changesBinding:true,
    inputSchema:object({name:string(120), task:enumeration(['classification','detection','segmentation','anomaly']), description:string(2000)}, ['name','task'])},
  {id:'import_dataset', title:'자료 가져오기', description:'직접 고른 폴더를 현재 프로젝트의 원본으로 가져옵니다.', changesBinding:true, inputSchema:object()},
  {id:'save_split', title:'자료 나누기', description:'학습·검증·시험 비율을 확인해 저장합니다.', changesBinding:true,
    inputSchema:object({train_percent:percent,val_percent:percent,test_percent:percent}, ['train_percent','val_percent','test_percent'])},
  {id:'configure_training', title:'학습 설정', description:'승인한 학습 품질·장치·배치 크기를 화면에 입력합니다.', changesBinding:true,
    inputSchema:{...object({preset:enumeration(['fast','precision']),device:enumeration(['auto','cpu','mps','cuda']),batch_size:{type:'integer',minimum:1,maximum:128}}), minProperties:1}},
  {id:'configure_input_recipe',title:'학습 입력 규칙 저장',description:'분류·영역 분할의 고정 ROI·회전·밝기·대비·크기 규칙을 현재 로컬 이미지로 미리 확인하고 다음 학습에 저장합니다.',changesBinding:true,
    inputSchema:object({input_recipe:recipeSchema},['input_recipe'])},
  {id:'generate_label_drafts', title:'라벨 초안 만들기', description:'모델 또는 문구로 초안을 준비합니다. 채택과 정답 확정은 직접 합니다.', changesBinding:false,
    inputSchema:object({mode:enumeration(['trained','phrase']),job_id:job,threshold:{type:'number',minimum:0,maximum:1},prompt:string(2000)})},
  {id:'start_training', title:'학습 시작', description:'현재 화면의 검증된 설정으로 학습을 시작하고 작업 번호를 확인합니다.', changesBinding:true, inputSchema:object()},
  {id:'evaluate_model', title:'모델 평가', description:'현재 원본과 정답에 묶인 모델 평가를 실행하고 결과를 확인합니다.', changesBinding:false, inputSchema:object({job_id:job})},
  {id:'save_flow', title:'검사 흐름 저장', description:'현재 모델을 쓰는 단일 모델 검사 흐름을 저장합니다.', changesBinding:false, inputSchema:object({job_id:job})},
  {id:'evaluate_flow', title:'검사 흐름 평가', description:'저장된 전체 검사 흐름을 평가합니다.', changesBinding:false, inputSchema:object()},
  {id:'inspect_test', title:'시험 검사', description:'현재 시험 검사 화면에서 준비한 입력을 실행합니다.', changesBinding:false, inputSchema:object()},
  {id:'export_report', title:'보고서 만들기', description:'현재 확인한 평가 결과를 보고서로 저장합니다.', changesBinding:false, inputSchema:object({format:enumeration(['html','json'])}, ['format'])},
  {id:'open_step', title:'화면 열기', description:'해당 작업 화면과 입력 위치를 엽니다.', changesBinding:false,
    inputSchema:object({step:{type:'integer',minimum:1,maximum:6},target:string(100,{pattern:'^[a-z][a-z0-9-]{1,99}$'})}, ['step'])},
];

function record(value: unknown, label: string): asserts value is Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value) || (Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null))
    throw new TaskValidationError(`${label}은 JSON 객체여야 합니다.`);
}
function exactKeys(value: Record<string, unknown>, keys: string[], label: string) {
  for (const key of Object.keys(value)) if (!keys.includes(key)) throw new TaskValidationError(`${label}에 허용하지 않는 입력이 있습니다: ${key}`);
}
function text(value: unknown, max: number, label: string) {
  if (typeof value !== 'string' || !value.trim() || value.length > max || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value))
    throw new TaskValidationError(`${label} 길이와 내용을 확인하세요.`);
}
function field(value: unknown, schema: Schema, name: string) {
  if(Array.isArray(schema.oneOf)){
    if(!(schema.oneOf as Schema[]).some(option=>{try{field(value,option,name);return true;}catch{return false;}}))throw new TaskValidationError(`${name} 입력 형식을 확인하세요.`);
  }else if(schema.type==='object'){
    record(value,name);const properties=schema.properties as Record<string,Schema>;
    exactKeys(value,Object.keys(properties),name);
    for(const key of schema.required as string[])if(!Object.hasOwn(value,key))throw new TaskValidationError(`${name} 필수 입력: ${key}`);
    for(const [key,child] of Object.entries(value))field(child,properties[key],key);
  }else if(schema.type==='array'){
    if(!Array.isArray(value)||value.length<Number(schema.minItems??0)||value.length>Number(schema.maxItems??128))throw new TaskValidationError(`${name} 연산 수를 확인하세요.`);
    value.forEach(child=>field(child,schema.items as Schema,name));
  }else if (schema.type === 'string') {
    text(value, schema.maxLength as number || 16384, name);
    if (schema.enum && !(schema.enum as string[]).includes(value as string)) throw new TaskValidationError(`${name} 값이 지원되지 않습니다.`);
    if (schema.pattern && !new RegExp(schema.pattern as string).test(value as string)) throw new TaskValidationError(`${name} 형식을 확인하세요.`);
  } else if (schema.type === 'number' || schema.type === 'integer') {
    if (typeof value !== 'number' || !Number.isFinite(value) || (schema.type === 'integer' && !Number.isInteger(value)) || value < (schema.minimum as number) || value > (schema.maximum as number))
      throw new TaskValidationError(`${name} 범위를 확인하세요.`);
  }
}

export function taskActionCatalog(): TaskActionDefinition[] {
  return JSON.parse(JSON.stringify(ACTION_CATALOG)) as TaskActionDefinition[];
}

/** Returns an independent JSON copy; no provider fields can become execution authority. */
export function validateTaskAction(action: unknown, args: unknown): Record<string, unknown> {
  const definition = ACTION_CATALOG.find(row => row.id === action);
  if (!definition) throw new TaskValidationError('도우미가 수행할 수 없는 행동입니다.');
  record(args, '행동 입력');
  const properties = definition.inputSchema.properties as Record<string, Schema>;
  exactKeys(args, Object.keys(properties), '행동 입력');
  for (const key of definition.inputSchema.required as string[]) if (!Object.hasOwn(args, key)) throw new TaskValidationError(`필수 입력이 없습니다: ${key}`);
  if (definition.inputSchema.minProperties && !Object.keys(args).length) throw new TaskValidationError('바꿀 학습 설정을 하나 이상 고르세요.');
  for (const [key, value] of Object.entries(args)) field(value, properties[key], key);
  if (action === 'save_split' && Math.abs((args.train_percent as number) + (args.val_percent as number) + (args.test_percent as number) - 100) > 1e-7)
    throw new TaskValidationError('자료 분할 비율의 합은 100이어야 합니다.');
  if (action === 'generate_label_drafts') {
    if ((args.job_id !== undefined && args.prompt !== undefined) || (args.mode === 'trained' && args.prompt !== undefined) || (args.mode === 'phrase' && (args.job_id !== undefined || args.prompt === undefined)))
      throw new TaskValidationError('초안 모델과 문구 입력을 함께 지정할 수 없습니다.');
  }
  if(action==='configure_input_recipe')validateInputRecipe(args.input_recipe as FixedInputRecipe,'classification');
  return JSON.parse(JSON.stringify(args)) as Record<string, unknown>;
}

export function validateTaskPlan(input: unknown): TaskPlanInput {
  record(input, '작업 계획'); exactKeys(input, ['goal','binding','steps'], '작업 계획');
  text(input.goal,4096,'작업 목표'); text(input.binding,16384,'현재 작업 문맥');
  if (!Array.isArray(input.steps) || input.steps.length < 1 || input.steps.length > 24) throw new TaskValidationError('작업 단계는 1개 이상 24개 이하여야 합니다.');
  const steps = input.steps.map(step => {
    record(step,'작업 단계'); exactKeys(step,['action','args','label'],'작업 단계'); text(step.label,160,'단계 이름');
    return {action:step.action as ActionId,args:validateTaskAction(step.action,step.args),label:step.label as string};
  });
  const result = {goal:input.goal as string,binding:input.binding as string,steps};
  if (new TextEncoder().encode(JSON.stringify(result)).byteLength > 65536) throw new TaskValidationError('작업 계획이 너무 큽니다.');
  return result;
}
export const validateTaskPlanInput = validateTaskPlan;
