import type {ProjectContext,ProjectSourceExpectation} from './api';
export interface LinkedSourceScope {key:string;context:ProjectContext;projectDir:string;expectedSource:ProjectSourceExpectation}
export interface LinkedSourcePlan {plan_id:string;plan_sha256:string;source:string;targets:{link:string;path:string;kind:'file'|'directory'}[];file_count:number;total_bytes:number;expires_in:number}
export interface LinkedSourceSnapshot {snapshot_id:string;manifest_sha256:string;source:string;file_count:number;total_bytes:number}
type RequestOptions={method:string;body:string;projectContext:ProjectContext};
type Runtime={available:()=>boolean;prepare:()=>Promise<void>;context:()=>ProjectContext|null;generation:()=>number|string;request:<T>(path:string,options:RequestOptions)=>Promise<T>};
type Binding={scope:string;generation:number|string};
const contextKey=(value:ProjectContext|null)=>value?JSON.stringify([value.workspace_id,value.project_id,value.actor_id,value.mode]):'';
const scopeKey=(scope:LinkedSourceScope)=>JSON.stringify([scope.key,contextKey(scope.context),scope.projectDir,scope.expectedSource]);
const sha=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const counts=(v:{file_count:number;total_bytes:number})=>Number.isSafeInteger(v.file_count)&&v.file_count>0&&v.file_count<=10000&&Number.isSafeInteger(v.total_bytes)&&v.total_bytes>=0&&v.total_bytes<=4*1024**3;
/** A localhost-only explicit action. Preview/copy never select a source or start a job. */
export class LinkedSourceClient {
 private plans=new Map<string,Binding>();private copies=new Map<string,Binding>();
 constructor(private readonly runtime:Runtime){}
 available():boolean{return this.runtime.available();}
 private capture(scope:LinkedSourceScope,current:()=>boolean,requireAvailable=true):Binding {
  if((requireAvailable&&!this.available())||!current()||contextKey(this.runtime.context())!==contextKey(scope.context)||!scope.expectedSource.source_dataset_dir)
   throw new Error('현재 로컬 프로젝트의 등록 원본과 계정 연결을 확인하세요.');
  return {scope:scopeKey(scope),generation:this.runtime.generation()};
 }
 private check(scope:LinkedSourceScope,binding:Binding,current:()=>boolean):void {
  const live=this.capture(scope,current);
  if(live.scope!==binding.scope||live.generation!==binding.generation)throw new Error('계정·프로젝트나 원본이 바뀌었습니다. 링크 대상을 다시 확인하세요.');
 }
 private bind(map:Map<string,Binding>,key:string,binding:Binding):void {if(map.size>=16)map.delete(map.keys().next().value!);map.set(key,binding);}
 private async post<T>(action:string,body:unknown,scope:LinkedSourceScope,binding:Binding,current:()=>boolean):Promise<T>{
  this.check(scope,binding,current);const value=await this.runtime.request<T>('/api/local-host/linked-sources/'+action,{method:'POST',body:JSON.stringify(body),projectContext:{...scope.context}});this.check(scope,binding,current);return value;
 }
 async preview(scope:LinkedSourceScope,current:()=>boolean):Promise<LinkedSourcePlan>{
  // Pin authority before the await; readiness may require a fresh native tab.
  // Preparation cannot change this binding, and the ready capability is still required before HTTP.
  const binding=this.capture(scope,current,false);await this.runtime.prepare();this.check(scope,binding,current);
  const value=await this.post<LinkedSourcePlan>('preview',{expected_source:{...scope.expectedSource}},scope,binding,current);
  if(typeof value.plan_id!=='string'||! /^[A-Za-z0-9_-]{20,128}$/.test(value.plan_id)||!sha(value.plan_sha256)||value.source!==scope.expectedSource.source_dataset_dir||!counts(value)
   ||!Array.isArray(value.targets)||!value.targets.length||value.targets.length>32||value.targets.some(target=>typeof target?.link!=='string'||typeof target.path!=='string'||!target.path.startsWith('/')||!['file','directory'].includes(target.kind)))
   throw new Error('서버의 링크 검토 결과를 확인하지 못했습니다. 다시 검토하세요.');
  this.bind(this.plans,JSON.stringify(value),binding);return value;
 }
 async materialize(scope:LinkedSourceScope,plan:LinkedSourcePlan,current:()=>boolean):Promise<LinkedSourceSnapshot|null>{
  const key=JSON.stringify(plan),binding=this.plans.get(key);if(!binding)throw new Error('현재 탭에서 링크 대상을 먼저 확인하세요.');
  this.check(scope,binding,current);this.plans.delete(key); // Uncertain delivery is never replayed.
  const value=await this.post<LinkedSourceSnapshot|{cancelled:true}>('materialize',{plan_id:plan.plan_id,plan_sha256:plan.plan_sha256},scope,binding,current);
  if('cancelled' in value&&value.cancelled===true)return null;
  const copy=value as LinkedSourceSnapshot;
  if(typeof copy.snapshot_id!=='string'||! /^[a-f0-9]{32}$/.test(copy.snapshot_id)||!sha(copy.manifest_sha256)||!counts(copy)
   ||copy.source!==scope.projectDir.replace(/\/$/,'')+'/.local-host-linked-sources/'+copy.snapshot_id+'/payload')throw new Error('검토된 프로젝트 복사본을 확인하지 못했습니다.');
  this.bind(this.copies,JSON.stringify(copy),binding);return copy;
 }
 async useCopy(scope:LinkedSourceScope,copy:LinkedSourceSnapshot,current:()=>boolean,onUse:(source:string)=>Promise<void>):Promise<void>{
  const binding=this.copies.get(JSON.stringify(copy));if(!binding)throw new Error('현재 탭에서 만든 복사본을 먼저 확인하세요.');
  const checked=await this.post<LinkedSourceSnapshot>('use-check',{snapshot_id:copy.snapshot_id,manifest_sha256:copy.manifest_sha256},scope,binding,current);
  if(JSON.stringify(checked)!==JSON.stringify(copy))throw new Error('프로젝트 복사본이 바뀌었습니다. 다시 검토하세요.');
  this.check(scope,binding,current);await onUse(checked.source);
 }
}
