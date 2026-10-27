/** Actor-scoped task records. This client never retries a mutation on ambiguity. */
import {request} from '../api';
import type {AssistantTask, TaskEvent, TaskPlanInput} from './types';

type Send = <T>(path:string, options?:{method?:string;body?:string;projectContext:null})=>Promise<T>;
export interface AssistantTaskClient {
  list():Promise<AssistantTask[]>;
  get(id:string):Promise<AssistantTask>;
  create(plan:TaskPlanInput):Promise<AssistantTask>;
  event(id:string,event:TaskEvent):Promise<AssistantTask>;
}
function identity(id:string):string {
  if(!/^task_[a-f0-9]{32}$/.test(id))throw new Error('작업 ID를 확인하세요.');
  return id;
}
function taskRecord(value:unknown):AssistantTask {
  const task=(value as {task?:AssistantTask}|null)?.task;
  if(!task||task.schema_version!==1||!/^task_[a-f0-9]{32}$/.test(task.id)
      ||!Number.isInteger(task.revision)||task.revision<1||!Array.isArray(task.steps))
    throw new Error('작업 기록 응답을 확인하지 못했습니다. 목록을 다시 읽으세요.');
  return task;
}
export function createTaskClient(send:Send=request):AssistantTaskClient {
  const base='/api/assistant-tasks';
  return {
    async list(){
      const value=await send<{tasks:AssistantTask[]}>(base,{projectContext:null});
      if(!Array.isArray(value?.tasks))throw new Error('작업 기록 목록을 확인하지 못했습니다.');
      return value.tasks.map(task=>taskRecord({task}));
    },
    async get(id){return taskRecord(await send(base+'/'+identity(id),{projectContext:null}));},
    async create(plan){return taskRecord(await send(base,{method:'POST',body:JSON.stringify(plan),projectContext:null}));},
    async event(id,event){return taskRecord(await send(base+'/'+identity(id)+'/events',{
      method:'POST',body:JSON.stringify(event),projectContext:null}));},
  };
}
export const assistantTaskClient=createTaskClient();
