import type {AnnotationItem,Category} from '../../types';
import type {FoundationSetup,FoundationOptions,RegionExample,SizeControls} from '../../services/foundationLabelingApi';
export function labelingScope(state:{projectDir:string|null;task?:string;project?:{id?:string;task?:string;active_labelset_id?:string;source_dataset_dir?:string|null}|null}):string {
  return JSON.stringify([state.projectDir,state.project?.id,state.project?.task||state.task,
    state.project?.active_labelset_id||'default',state.project?.source_dataset_dir]);
}
/** What a point request takes from the class palette: the active class (id and name) and the name→id map it sends as
 *  class_ids. Order and colour are left out: every label reload rebuilds the palette (labelCategoryPalette), which
 *  reorders and recolours it without changing either. */
export function quickPointClassKey(active:{id?:number;name?:string}|null|undefined,categories:Category[]):string {
  const classIds=Object.fromEntries(categories.filter(c=>c.id>0&&c.id<=255).map(c=>[c.name,c.id]));
  return JSON.stringify([active?.id??null,active?.name??null,Object.entries(classIds).sort(([a],[b])=>a<b?-1:a>b?1:0)]);
}
/** The same key read from a remembered [activeCategory, categories] snapshot; an unreadable one matches nothing. */
export function quickPointClassKeyOf(context:string|undefined):string|null {
  try {const [active,categories]=JSON.parse(context??'');return Array.isArray(categories)?quickPointClassKey(active,categories):null;}
  catch {return null;}
}
export function foundationAllowed(setup:FoundationSetup|null,prompt:string):boolean {
  return !!setup?.providers.foundation.ready&&(!prompt.trim()||setup.providers.grounding_dino.ready);
}
export function buildFoundationRequest(imagePath:string,options:FoundationOptions,setup:FoundationSetup):Record<string,unknown> {
  return {backend:'foundation',image_path:imagePath,...options,labelset_id:setup.labelset_id,labelset_version:setup.labelset_version};
}
export function regionExample(imagePath:string,annotation:AnnotationItem|undefined):RegionExample|null {
  if(!annotation)return null;
  let roi=annotation.bbox;
  const points=annotation.polygon||annotation.points;
  if(!roi&&points?.length){const xs=points.map(p=>p[0]);const ys=points.map(p=>p[1]);roi=[Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)];}
  return roi&&roi[2]>roi[0]&&roi[3]>roi[1]?{image_path:imagePath,roi}:null;
}
export function brushEditTarget(annotations:AnnotationItem[],selectedId:string|null,categoryId:number):AnnotationItem|undefined {
  return annotations.find(a=>a.type==='brush_mask'&&a.id===selectedId)||annotations.find(a=>a.type==='brush_mask'&&a.category_id===categoryId);
}
export function buildModelRequest(jobId:string,imagePath:string,threshold:number,keywords:string[],options:SizeControls&{device:string}):Record<string,unknown>{
  return {job_id:jobId,image_path:imagePath,threshold,keywords,...options};
}
export function labelCategoryPalette(existing:Category[],annotations:AnnotationItem[],maskClasses:{id:number;name:string;color:string}[]=[]):Category[]{
  const result:Category[]=maskClasses.filter(c=>c.id>0).map(c=>({...c}));
  for(const a of annotations){if(a.type==='tag'||!a.category_id)continue;const found=result.findIndex(c=>c.name.toLowerCase()===a.label.toLowerCase());const c={id:a.category_id,name:a.label,color:a.color||'#22d3ee'};if(found>=0)result[found]=c;else if(!result.some(c=>c.id===a.category_id))result.push(c);}
  const names=new Set(result.map(c=>c.name.toLowerCase()));const used=new Set(result.map(c=>c.id));
  for(const c of existing){if(names.has(c.name.toLowerCase()))continue;let id=c.id;if(used.has(id)){id=1;while(used.has(id))id++;}result.push({...c,id});used.add(id);names.add(c.name.toLowerCase());}
  return result;
}
