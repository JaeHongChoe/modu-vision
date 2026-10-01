import {useEffect,useState} from 'react';
import {useProjectStore} from '../../stores/useProjectStore';
import {useComputeStore} from '../../stores/useComputeStore';
import {getApiPersistenceIdentity} from '../../services/api';
import {readTaskHandoff} from './taskHandoff';
export function useTaskHandoff(family?:string,includeSearch=false){
 const state=useProjectStore();const compute=useComputeStore();const [,refresh]=useState(0);
 useEffect(()=>{const changed=()=>refresh(value=>value+1);window.addEventListener('vision-task-handoff',changed);return()=>window.removeEventListener('vision-task-handoff',changed);},[]);
 const row=readTaskHandoff(localStorage,{...state,...compute,apiTransportIdentity:getApiPersistenceIdentity()},family);return !includeSearch&&family&&row?.kind==='automated'&&row.status!=='completed'?null:row;
}
