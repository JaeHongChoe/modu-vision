import {getApiPersistenceIdentity} from '../../services/api';
import {useComputeStore} from '../../stores/useComputeStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {saveModelFlowHandoff} from '../flowchart/modelFlowHandoff';
export async function openModelFlow(family:string,modelId:string,datasetPath=''){
 const project=useProjectStore.getState(),compute=useComputeStore.getState();
 saveModelFlowHandoff(localStorage,{...project,...compute,apiTransportIdentity:getApiPersistenceIdentity()},family,modelId,datasetPath);
 await project.setStep(5);
}
