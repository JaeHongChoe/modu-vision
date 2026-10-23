import {getApiPersistenceIdentity,getProjectContextGeneration} from '../../services/api';
import {foundationLabelingApi as provider,type FoundationOptions,type FoundationProposal} from '../../services/foundationLabelingApi';
import {useAnnotationStore} from '../../stores/useAnnotationStore';
import {useFoundationPromptStore} from '../../stores/useFoundationPromptStore';
import {useModelAssistRunStore} from '../../stores/useModelAssistRunStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {buildFoundationRequest,foundationAllowed,labelingScope} from './foundationRequest';

interface Callbacks {isBlocked:()=>boolean;onCreated:(proposal:FoundationProposal)=>void;onError:(error:unknown)=>void;onRunning:(running:boolean)=>void}
const categoryContext=()=>{const state=useAnnotationStore.getState();return JSON.stringify([state.activeCategory,state.categories]);};
const annotationContext=()=>{const state=useAnnotationStore.getState();return JSON.stringify([
  state.currentImage?.file_path,state.isDirty,state.annotationLoadStatus,state.activeCategory,state.categories,
]);};

/** One visit's opt-in point runner, mounted outside the popover. New points are the only trigger: no replay, queue,
 * retries, cancellation, adoption or navigation. A stopped controller lets its owned request finish and releases its
 * own operation count; its result cannot reach a later visit, even when that visit opens the same image again. */
export function startFoundationQuickPointController(callbacks:Callbacks):()=>void {
  let active=true,running=false,annotationRevision=0,lastAnnotation=annotationContext();
  const prompts=useFoundationPromptStore;
  const bindProject=()=>prompts.getState().bindProject(labelingScope(useProjectStore.getState()));
  const bindImage=()=>prompts.getState().bind(useAnnotationStore.getState().currentImage?.file_path||'');
  bindProject();bindImage();
  const unsubscribeProject=useProjectStore.subscribe(bindProject);
  const unsubscribeAnnotation=useAnnotationStore.subscribe(()=>{
    const current=annotationContext();if(current!==lastAnnotation){lastAnnotation=current;annotationRevision++;}bindImage();
    const state=prompts.getState();
    if(state.quickPointEnabled&&state.quickOptions?.categoryContext!==categoryContext()){
      state.setQuickPointEnabled(false);
      if(active)callbacks.onError(new Error('후보 클래스가 바뀌어 점 입력 후 후보 만들기를 껐습니다. 클래스를 확인하고 다시 켜세요.'));
    }
  });
  const readyToRun=()=>{const annotation=useAnnotationStore.getState();return !callbacks.isBlocked()
    &&!useProjectStore.getState().isProjectBusy&&!annotation.isDirty&&annotation.annotationLoadStatus==='ready';};
  const unsubscribePrompt=prompts.subscribe((state,previous)=>{
    if(state.pointRevision===previous.pointRevision||!active||!state.quickPointEnabled)return;
    const settings=state.quickOptions,imagePath=state.imagePath,scope=state.projectScope;
    if(running||useModelAssistRunStore.getState().activeOperations>0||!readyToRun()||!settings?.ready||!settings.options.label.trim()||!imagePath)return;
    if(scope!==labelingScope(useProjectStore.getState())||imagePath!==useAnnotationStore.getState().currentImage?.file_path)return;
    if(settings.categoryContext!==categoryContext()){state.setQuickPointEnabled(false);return;}
    const task=useProjectStore.getState().project?.task;if(task!=='detection'&&task!=='segmentation')return;
    const revision=annotationRevision,transport=getApiPersistenceIdentity(),generation=getProjectContextGeneration();
    const same=()=>active&&prompts.getState().quickPointEnabled&&prompts.getState().promptRevision===state.promptRevision
      &&prompts.getState().quickOptionsRevision===state.quickOptionsRevision&&annotationRevision===revision&&readyToRun()
      &&labelingScope(useProjectStore.getState())===scope&&useAnnotationStore.getState().currentImage?.file_path===imagePath
      &&getApiPersistenceIdentity()===transport&&getProjectContextGeneration()===generation;
    const options:FoundationOptions=JSON.parse(JSON.stringify({...settings.options,points:state.points,boxes:state.boxes,
      exclude_regions:settings.imagePath===imagePath?settings.options.exclude_regions:[],
      class_ids:Object.fromEntries(useAnnotationStore.getState().categories.filter(c=>c.id>0&&c.id<=255).map(c=>[c.name,c.id])),
    }));
    running=true;useModelAssistRunStore.getState().begin();callbacks.onRunning(true);
    void (async()=>{
      try {
        // The current fingerprint/label set is read for every point, just as the manual generate path does.
        const setup=await provider.setup();if(!same())return;
        if(setup.labelset_id!==(useProjectStore.getState().project?.active_labelset_id||'default')){
          prompts.getState().setQuickPointEnabled(false);
          callbacks.onError(new Error('라벨 세트가 바뀌어 점 입력 후 후보 만들기를 껐습니다. 프로젝트를 확인하고 다시 켜세요.'));return;
        }
        if(!foundationAllowed(setup,options.prompt)||['empty','not_safetensors','truncated'].includes(setup.providers.foundation.weights_check||''))
          throw new Error('SAM2 모델이 준비되지 않았습니다. 모델 파일·실행 장치를 확인한 뒤 후보를 다시 만드세요.');
        const next=await provider.generate(buildFoundationRequest(imagePath,options,setup));
        if(same()&&next.image_path===imagePath)callbacks.onCreated(next);
      } catch(error) {if(same())callbacks.onError(error);}
      finally {running=false;useModelAssistRunStore.getState().end();if(active)callbacks.onRunning(false);}
    })();
  });
  return ()=>{active=false;unsubscribePrompt();unsubscribeProject();unsubscribeAnnotation();prompts.getState().setQuickPointEnabled(false);};
}
