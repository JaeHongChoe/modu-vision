import {useEffect,useRef,useState} from 'react';
import {api,getApiPersistenceIdentity,getProjectContext,getProjectContextGeneration,subscribeProjectContext,type LibraryImage} from '../../services/api';
import type {ImageMeta} from '../../types';
import {defaultCohort} from './flowPackageRelease';
import {parityCohortStorageKey,pickParityImage,readParityCohort,readParityPaths,resolveParityCohort,type ParityPick,type UnresolvedParityPick} from './parityCohort';

interface Scope {projectId?:string;projectDir:string|null;source:string;task:string;labelset?:string;deliveryKey:string;legacyImages:ImageMeta[];legacyReady?:boolean}
interface State {token:string;phase:'loading'|'ready'|'failed';mode:'available'|'unavailable';picks:ParityPick[];unresolved:UnresolvedParityPick[];paths:string[];error:string}
const empty=(token:string):State=>({token,phase:'loading',mode:'available',picks:[],unresolved:[],paths:[],error:''});
/** Browser-local convenience selection. Identity resolution is mandatory before any saved image becomes executable. */
export function useParityCohort(scope:Scope){
  const [authority,setAuthority]=useState(getProjectContext);
  useEffect(()=>subscribeProjectContext(setAuthority),[]);
  const generation=getProjectContextGeneration();
  const key=scope.projectId&&scope.source&&(!authority||authority.project_id===scope.projectId)?parityCohortStorageKey({backend:getApiPersistenceIdentity(),workspace:authority?.workspace_id||'local',actor:authority?.actor_id||'local',project:scope.projectId,projectDir:scope.projectDir||'',source:scope.source,task:scope.task,labelset:scope.labelset||'default'}):null;
  const token=JSON.stringify([key,scope.deliveryKey,generation]);
  const live=useRef(token);live.current=token;
  const current=()=>live.current===token&&getProjectContextGeneration()===generation;
  const [attempt,setAttempt]=useState(0),[state,setState]=useState<State>(()=>empty(token));
  const legacySignature=JSON.stringify(scope.legacyImages.map(row=>row.file_path));
  useEffect(()=>{
    let active=true;setState(empty(token));if(!key)return;
    const valid=()=>active&&current();
    const restore=async()=>{
      try{
        const saved=readParityCohort(localStorage.getItem(key),true);
        try{
          const answer=await api.library.resolve(saved);
          if(valid())setState({...empty(token),phase:'ready',...resolveParityCohort(saved,answer.results)});
        }catch(cause){
          if((cause as {status?:number}).status!==409)throw cause;
          if(!valid()||scope.legacyReady===false)return;
          const raw=localStorage.getItem(key+':paths');
          const paths=raw===null?defaultCohort(scope.legacyImages.map(row=>row.file_path)):readParityPaths(raw,true);
          setState({...empty(token),phase:'ready',mode:'unavailable',paths});
        }
      }catch(cause){if(valid())setState({...empty(token),phase:'failed',error:`저장된 검증 이미지 선택을 확인하지 못했습니다: ${cause instanceof Error?cause.message:String(cause)}`});}
    };
    void restore();return()=>{active=false;};
  },[token,attempt,legacySignature,scope.legacyReady]);
  const visible=state.token===token?state:empty(token);
  useEffect(()=>{
    if(!key||state.token!==token||state.phase!=='ready'||!current())return;
    try{if(state.mode==='available')localStorage.setItem(key,JSON.stringify([...state.picks,...state.unresolved]));else localStorage.setItem(key+':paths',JSON.stringify(state.paths));}
    catch{setState(previous=>previous.token===token?{...previous,error:'선택을 이 브라우저에 저장하지 못했습니다. 앱을 다시 열면 복원되지 않을 수 있습니다.'}:previous);}
  },[state.picks,state.unresolved,state.paths,state.mode,state.phase,token,key]);
  const change=(update:(previous:State)=>State)=>{if(!current())return;setState(previous=>previous.token===token&&previous.phase==='ready'?update(previous):previous);};
  return {...visible,ready:Boolean(key&&visible.phase==='ready'),
    pick:(image:LibraryImage)=>change(previous=>({...previous,...pickParityImage(previous.picks,previous.unresolved,image)})),
    pathsChange:(update:(paths:string[])=>string[])=>change(previous=>({...previous,paths:update(previous.paths)})),
    remove:(imageUuid:string)=>change(previous=>({...previous,picks:previous.picks.filter(row=>row.image_uuid!==imageUuid),unresolved:previous.unresolved.filter(row=>row.image_uuid!==imageUuid)})),
    clear:()=>{
      if(!current())return;
      if(key&&visible.phase==='failed'){
        // Recovery is explicit: a failed restore never replaces saved bytes automatically.
        try{localStorage.setItem(key,'[]');localStorage.setItem(key+':paths','[]');setAttempt(value=>value+1);}
        catch{setState(previous=>({...previous,error:'선택 목록을 초기화하지 못했습니다.'}));}
      }else change(previous=>({...previous,picks:[],unresolved:[],paths:[]}));
    },
    unavailable:()=>{if(current())setAttempt(value=>value+1);},
    retry:()=>{if(current())setAttempt(value=>value+1);},
  };
}
