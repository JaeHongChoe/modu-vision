import {create} from 'zustand';
import type {FoundationOptions,FoundationPoint} from '../services/foundationLabelingApi';
interface QuickPointOptions {imagePath:string;options:FoundationOptions;ready:boolean;categoryContext:string}
interface PromptState {
  projectScope:string;imagePath:string;points:FoundationPoint[];boxes:number[][];pointLabel:0|1;displaySource:string|null;displaySourcePath:string|null;
  promptRevision:number;pointRevision:number;quickPointEnabled:boolean;quickOptions:QuickPointOptions|null;quickOptionsRevision:number;
  bindProject:(scope:string)=>void;bind:(path:string)=>void;addPoint:(point:FoundationPoint)=>void;addBox:(box:number[])=>void;clear:()=>void;setPointLabel:(label:0|1)=>void;setDisplaySource:(path:string,url:string)=>void;
  configureQuickPoint:(scope:string,imagePath:string,options:FoundationOptions,ready:boolean,categoryContext:string)=>void;setQuickPointEnabled:(enabled:boolean)=>void;
}
export const useFoundationPromptStore=create<PromptState>((set,get)=>({
  projectScope:'',imagePath:'',points:[],boxes:[],pointLabel:1,displaySource:null,displaySourcePath:null,
  promptRevision:0,pointRevision:0,quickPointEnabled:false,quickOptions:null,quickOptionsRevision:0,
  bindProject:(scope)=>{if(get().projectScope!==scope)set(s=>({projectScope:scope,points:[],boxes:[],displaySource:null,displaySourcePath:null,
    promptRevision:s.promptRevision+1,quickPointEnabled:false,quickOptions:null,quickOptionsRevision:s.quickOptionsRevision+1}));},
  bind:(path)=>{if(get().imagePath!==path)set(s=>({imagePath:path,points:[],boxes:[],displaySource:null,displaySourcePath:null,promptRevision:s.promptRevision+1}));},
  addPoint:(point)=>set(s=>({points:[...s.points,point],promptRevision:s.promptRevision+1,pointRevision:s.pointRevision+1})),
  addBox:(box)=>set(s=>({boxes:[...s.boxes,box],promptRevision:s.promptRevision+1})),
  clear:()=>set(s=>({points:[],boxes:[],promptRevision:s.promptRevision+1})),setPointLabel:(pointLabel)=>set({pointLabel}),
  setDisplaySource:(path,url)=>set({displaySourcePath:path,displaySource:url}),
  // Session memory only. The controller survives the popover, but leaving the visit or changing project/label set disarms it.
  configureQuickPoint:(scope,imagePath,options,ready,categoryContext)=>{
    if(get().projectScope!==scope)return;
    const next:QuickPointOptions={imagePath,options:JSON.parse(JSON.stringify({...options,points:[],boxes:[]})),ready,categoryContext};
    if(JSON.stringify(get().quickOptions)!==JSON.stringify(next))set(s=>({quickOptions:next,quickOptionsRevision:s.quickOptionsRevision+1}));
  },
  setQuickPointEnabled:(enabled)=>{const state=get();const next=enabled&&!!state.projectScope&&!!state.imagePath&&!!state.quickOptions?.ready;
    if(state.quickPointEnabled!==next)set(s=>({quickPointEnabled:next,quickOptionsRevision:s.quickOptionsRevision+1}));},
}));
