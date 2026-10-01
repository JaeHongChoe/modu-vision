import {useRef} from 'react';
import {useProjectStore} from '../../stores/useProjectStore';
import {useComputeStore} from '../../stores/useComputeStore';
export function useDeliveryScope(extra=''){
 const project=useProjectStore(s=>s.project),projectDir=useProjectStore(s=>s.projectDir),revision=useComputeStore(s=>s.transportRevision);
 const key=JSON.stringify([projectDir,project?.id,project?.task,project?.source_dataset_dir,project?.active_labelset_id,revision,extra]);
 const scope=useRef({key});if(scope.current.key!==key)scope.current={key};return {scope,key,project,projectDir};
}
