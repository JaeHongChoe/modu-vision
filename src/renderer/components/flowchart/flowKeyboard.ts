import type {useFlowchartStore} from '../../stores/useFlowchartStore';

type State=ReturnType<typeof useFlowchartStore.getState>;
type KeyboardState=Pick<State,'pipeline'|'isRunning'|'isLoading'|'isSaving'|'isDraftSaving'|'historyGroupStart'|'beginHistoryGroup'|'endHistoryGroup'|'moveNode'|'selectNode'>;

/** One held-arrow gesture is one existing history group; moves remain layout-only. */
export function createKeyboardNodeMovement(getState:()=>KeyboardState,announce:(message:string)=>void){
  let movingNode:string|null=null;
  const end=()=>{if(movingNode!==null){movingNode=null;getState().endHistoryGroup();}};
  return {end,move:(id:string,dx:number,dy:number,disabled:boolean)=>{
    const state=getState();
    if(disabled||state.isRunning||state.isLoading||(state.isSaving&&!state.isDraftSaving)){end();return;}
    const node=state.pipeline?.nodes.find(row=>row.id===id);
    if(!node||!Number.isFinite(dx)||!Number.isFinite(dy)){end();return;}
    if(movingNode!==id){end();movingNode=id;}
    if(!getState().historyGroupStart)getState().beginHistoryGroup();
    state.selectNode(id);
    state.moveNode(id,{x:node.position.x+dx,y:node.position.y+dy});
    const moved=getState().pipeline?.nodes.find(row=>row.id===id);
    if(moved)announce(`${moved.data.label||id} 위치 ${moved.position.x}, ${moved.position.y}`);
  }};
}

/** Reveal focused content inside its own scroll owner, without moving page panels. */
export function revealFlowNode(canvas:HTMLElement|null,element:HTMLElement|null){
  if(!canvas||!element)return;
  const view=canvas.getBoundingClientRect(),node=element.getBoundingClientRect(),padding=12;
  const left=node.left<view.left?node.left-view.left-padding:node.right>view.right?node.right-view.right+padding:0;
  const top=node.top<view.top?node.top-view.top-padding:node.bottom>view.bottom?node.bottom-view.bottom+padding:0;
  if(left||top)canvas.scrollBy({left,top,behavior:'auto'});
}

export function focusFlowNodeInCanvas(canvas:HTMLElement|null,id:string){
  const element=canvas?.querySelector<HTMLElement>(`[data-flow-node-id="${CSS.escape(id)}"] [data-flow-node-focus]`)||null;
  if(element){element.focus({preventScroll:true});revealFlowNode(canvas,element);}
}

/** A deferred layout repaint must not steal focus after Tab or another action. */
export function revealCurrentlyFocusedFlowNode(canvas:HTMLElement|null,id:string){
  const element=canvas?.querySelector<HTMLElement>(`[data-flow-node-id="${CSS.escape(id)}"] [data-flow-node-focus]`)||null;
  if(element&&element.ownerDocument.activeElement===element)revealFlowNode(canvas,element);
}
