import {useEffect,useRef,useState} from 'react';
import {api,getApiPersistenceIdentity,getProjectContext,getProjectContextGeneration,subscribeProjectContext,type LibraryImage,type LibraryResolution} from '../../services/api';
import type {VisionTask} from '../../types';

interface Scope {projectId?:string;projectDir:string|null;source:string;task:VisionTask;labelset?:string;deliveryKey:string}
interface Choice {image_uuid:string;sha256:string;relative_path:string;file_name:string;file_path:string}
type Status='empty'|LibraryResolution['status'];
interface State {binding:string;phase:'loading'|'ready'|'failed';mode:'indexed'|'legacy';selection:Choice|null;path:string;status:Status;error:string}
const empty=(binding:string):State=>({binding,phase:'loading',mode:'indexed',selection:null,path:'',status:'empty',error:''});
function readChoice(raw:string|null):Choice|null {
  const row:unknown=JSON.parse(raw??'null');
  if(row===null)return null;
  if(!row||typeof row!=='object'||Array.isArray(row))throw new Error('저장된 이미지 선택 형식이 손상되었습니다. 선택 해제로 초기화하세요.');
  const choice=row as Choice;
  if(![choice.image_uuid,choice.relative_path,choice.file_name,choice.file_path].every(value=>typeof value==='string'&&value.length>0)
    ||typeof choice.sha256!=='string'||!/^[a-f0-9]{64}$/.test(choice.sha256))throw new Error('저장된 이미지 UUID·해시·경로를 확인하지 못했습니다. 선택 해제로 초기화하세요.');
  return {image_uuid:choice.image_uuid,sha256:choice.sha256,relative_path:choice.relative_path,file_name:choice.file_name,file_path:choice.file_path};
}
function readPath(raw:string|null):string {
  const value:unknown=JSON.parse(raw??'null');
  if(value===null)return '';
  if(typeof value!=='string'||!value)throw new Error('저장된 경로 선택 형식이 손상되었습니다. 선택 해제로 초기화하세요.');
  return value;
}
function resolvedStatus(choice:Choice|null,results:unknown):Status {
  const invalid=():never=>{throw new Error('서버의 이미지 확인 응답 형식이 올바르지 않습니다. 다시 확인하거나 선택 해제하세요.');};
  if(!Array.isArray(results)||results.length!==(choice?1:0))return invalid();
  if(!choice)return 'empty';
  const row=results[0] as LibraryResolution;
  const statuses=['found','changed','unreadable','moved','missing'];
  const validCurrent=(value:unknown):boolean=>{
    if(!value||typeof value!=='object')return false;
    const item=value as NonNullable<LibraryResolution['current']>;
    return [item.image_uuid,item.relative_path,item.file_path].every(text=>typeof text==='string'&&text.length>0)
      &&(item.sha256===null||typeof item.sha256==='string'&&/^[a-f0-9]{64}$/.test(item.sha256))
      &&(item.valid===0||item.valid===1);
  };
  if(!row||typeof row!=='object'||row.image_uuid!==choice.image_uuid||row.sha256!==choice.sha256
    ||!statuses.includes(row.status)||!Array.isArray(row.candidates)||!row.candidates.every(validCurrent)
    ||!(row.current===null||validCurrent(row.current)))return invalid();
  if(row.status!=='found')return row.status;
  if(!row.current||!row.current.sha256||row.current.image_uuid!==choice.image_uuid||row.current.sha256!==choice.sha256)return invalid();
  if(!row.current.valid)return 'unreadable';
  if(row.current.file_path!==choice.file_path||row.current.relative_path!==choice.relative_path)return 'moved';
  return 'found';
}
/** One browser-local manual image choice; saved bytes are usable only after identity or legacy listing revalidation. */
export function useOperatorImageChoice(scope:Scope){
  const [authority,setAuthority]=useState(getProjectContext);
  const mounted=useRef(true);
  useEffect(()=>{mounted.current=true;const unsubscribe=subscribeProjectContext(setAuthority);return()=>{mounted.current=false;unsubscribe();};},[]);
  const generation=getProjectContextGeneration();
  const storageKey=scope.projectId&&scope.source&&(!authority||authority.project_id===scope.projectId)
    ?`modu:operator-image:v1:${JSON.stringify([getApiPersistenceIdentity(),authority?.workspace_id||'local',authority?.actor_id||'local',scope.projectId,scope.projectDir||'',scope.source,scope.task,scope.labelset||'default'])}`:null;
  const binding=JSON.stringify([storageKey,scope.deliveryKey,generation]);
  const [attempt,setAttempt]=useState(0),[state,setState]=useState<State>(()=>empty(binding));
  const token=JSON.stringify([binding,attempt]),live=useRef(token),operation=useRef(0);live.current=token;
  const current=()=>mounted.current&&live.current===token&&getProjectContextGeneration()===generation;
  const fail=(cause:unknown)=>setState(previous=>previous.binding===binding?{...previous,phase:'failed',error:`저장된 검사 이미지 선택을 확인하지 못했습니다: ${cause instanceof Error?cause.message:String(cause)}`}:previous);
  const legacyCheck=async(path:string)=>{
    const listing=await api.dataset.getImages({folder_path:scope.source,task:scope.task,limit:64});
    return (path?listing.items.some(row=>row.file_path===path)?'found':'missing':'empty') as Status;
  };
  useEffect(()=>{
    let active=true;const sequence=++operation.current;
    const valid=()=>active&&current()&&sequence===operation.current;
    setState(previous=>previous.binding===binding?{...previous,phase:'loading',error:''}:empty(binding));
    if(!storageKey)return ()=>{active=false;};
    const restore=async()=>{
      try{
        const selection=readChoice(localStorage.getItem(storageKey));
        if(valid())setState({...empty(binding),selection,path:selection?.file_path||''});
        try{
          const answer=await api.library.resolve(selection?[selection]:[]);
          if(valid())setState({...empty(binding),phase:'ready',selection,path:selection?.file_path||'',status:resolvedStatus(selection,answer.results)});
        }catch(cause){
          if((cause as {status?:number}).status!==409)throw cause;
          if(!valid())return;
          const rawPath=localStorage.getItem(storageKey+':path');
          // A vanished index must not silently turn a saved identity into an executable path-only choice.
          const unresolvedIdentity=selection;
          const path=unresolvedIdentity?.file_path||readPath(rawPath);
          setState({...empty(binding),mode:'legacy',selection:unresolvedIdentity,path});
          const listedStatus=await legacyCheck(path),status=unresolvedIdentity?'missing':listedStatus;
          if(valid())setState({...empty(binding),phase:'ready',mode:'legacy',selection:unresolvedIdentity,path,status});
        }
      }catch(cause){if(valid())fail(cause);}
    };
    void restore();return()=>{active=false;};
  },[token]);
  const visible=state.binding===binding?state:empty(binding);
  const write=(mode:State['mode'],selection:Choice|null,path:string)=>{
    if(!storageKey)return;
    localStorage.setItem(mode==='indexed'?storageKey:storageKey+':path',JSON.stringify(mode==='indexed'?selection:path||null));
  };
  const canChange=()=>current()&&Boolean(storageKey)&&visible.phase==='ready';
  const select=async(image:LibraryImage|null)=>{
    if(!canChange())return;
    if(!image){clear();return;}
    if(!image.valid||!image.sha256||!/^[a-f0-9]{64}$/.test(image.sha256))return;
    const selection=readChoice(JSON.stringify(image));if(!selection)return;
    const sequence=++operation.current;setState({...empty(binding),selection,path:selection.file_path});
    try{
      const answer=await api.library.resolve([selection]);
      if(!current()||sequence!==operation.current)return;
      const status=resolvedStatus(selection,answer.results);write('indexed',selection,selection.file_path);
      setState({...empty(binding),phase:'ready',selection,path:selection.file_path,status});
    }catch(cause){if(current()&&sequence===operation.current)fail(cause);}
  };
  const selectLegacy=async(path:string)=>{
    if(!canChange()||visible.mode!=='legacy')return;
    if(!path){clear();return;}
    const sequence=++operation.current;setState({...empty(binding),mode:'legacy',path});
    try{
      const status=await legacyCheck(path);if(!current()||sequence!==operation.current)return;
      if(status==='found'){
        write('legacy',null,path);
        // Retire the indexed choice only after a successful explicit path check and write.
        if(storageKey)localStorage.setItem(storageKey,'null');
      }
      setState({...empty(binding),phase:'ready',mode:'legacy',path,status});
    }catch(cause){if(current()&&sequence===operation.current)fail(cause);}
  };
  const clear=()=>{
    if(!current()||!storageKey)return;
    ++operation.current;
    try{localStorage.setItem(storageKey,'null');localStorage.setItem(storageKey+':path','null');setState({...empty(binding),mode:visible.mode});setAttempt(value=>value+1);}
    catch(cause){fail(cause);}
  };
  const refresh=()=>{if(current()){++operation.current;setState(previous=>previous.binding===binding?{...previous,phase:'loading',error:''}:previous);setAttempt(value=>value+1);}};
  const renderOperation=operation.current;
  const verifyForInspect=async()=>{
    if(!current()||renderOperation!==operation.current||!storageKey||visible.phase!=='ready'||visible.status!=='found')throw new Error('선택한 검사 이미지를 다시 확인하세요.');
    const sequence=++operation.current;
    setState(previous=>({...previous,phase:'loading',error:''}));
    try{
      const status=visible.mode==='legacy'?await legacyCheck(visible.path):resolvedStatus(visible.selection,(await api.library.resolve(visible.selection?[visible.selection]:[])).results);
      if(!current()||sequence!==operation.current)throw new Error('검사 이미지 선택 문맥이 바뀌었습니다.');
      setState({...visible,phase:'ready',status});
      if(status!=='found')throw new Error('선택한 이미지가 바뀌었거나 사용할 수 없습니다. 다시 선택하세요.');
      return visible.path;
    }catch(cause){
      if(current()&&sequence===operation.current)fail(cause);
      throw cause;
    }
  };
  return {...visible,token,storageKey,verifyForInspect,isCurrent:()=>current()&&operation.current===renderOperation,canInspect:Boolean(storageKey&&visible.phase==='ready'&&visible.status==='found'),select,selectLegacy,clear,refresh};
}
