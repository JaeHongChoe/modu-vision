import {create} from 'zustand';
import type {FoundationPoint} from '../services/foundationLabelingApi';
interface PromptState {projectScope:string;imagePath:string;points:FoundationPoint[];boxes:number[][];pointLabel:0|1;displaySource:string|null;displaySourcePath:string|null;bindProject:(scope:string)=>void;bind:(path:string)=>void;addPoint:(point:FoundationPoint)=>void;addBox:(box:number[])=>void;clear:()=>void;setPointLabel:(label:0|1)=>void;setDisplaySource:(path:string,url:string)=>void}
export const useFoundationPromptStore=create<PromptState>((set,get)=>({
  projectScope:'',imagePath:'',points:[],boxes:[],pointLabel:1,displaySource:null,displaySourcePath:null,
  bindProject:(scope)=>{if(get().projectScope!==scope)set({projectScope:scope,points:[],boxes:[],displaySource:null,displaySourcePath:null});},
  bind:(path)=>{if(get().imagePath!==path)set({imagePath:path,points:[],boxes:[],displaySource:null,displaySourcePath:null});},
  addPoint:(point)=>set(s=>({points:[...s.points,point]})),addBox:(box)=>set(s=>({boxes:[...s.boxes,box]})),
  clear:()=>set({points:[],boxes:[]}),setPointLabel:(pointLabel)=>set({pointLabel}),
  setDisplaySource:(path,url)=>set({displaySourcePath:path,displaySource:url}),
}));
