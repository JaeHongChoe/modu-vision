export function cropPoint(bounds:{left:number;top:number;width:number;height:number},size:readonly[number,number],client:readonly[number,number]):[number,number]|null {
  const [w,h]=size;const scale=Math.min(bounds.width/w,bounds.height/h);if(!Number.isFinite(scale)||scale<=0)return null;
  const x=(client[0]-bounds.left-(bounds.width-w*scale)/2)/scale;const y=(client[1]-bounds.top-(bounds.height-h*scale)/2)/scale;
  return x>=0&&x<=w&&y>=0&&y<=h?[Math.round(x),Math.round(y)]:null;
}
export function cropRectangle(a:readonly[number,number],b:readonly[number,number],size:readonly[number,number]):[number,number,number,number]{
  return [Math.max(0,Math.floor(Math.min(a[0],b[0]))),Math.max(0,Math.floor(Math.min(a[1],b[1]))),Math.min(size[0],Math.ceil(Math.max(a[0],b[0]))),Math.min(size[1],Math.ceil(Math.max(a[1],b[1])))];
}
interface Storage {getItem:(key:string)=>string|null;setItem:(key:string,value:string)=>void;removeItem:(key:string)=>void}
const originKey=(scope:string,kind='evaluation')=>`modu-${kind}-return:${scope}`;
export interface ReviewOriginContext {evaluation_id?:string;comparison_id?:string;image_id:string|null;file_path?:string;product_filter?:string;lot_filter?:string}
export function rememberReviewOrigin(storage:Storage,scope:string,id:string,imageId?:string,filePath?:string,kind:'evaluation'|'comparison'='evaluation',filters?:{product_filter:string;lot_filter:string}){storage.setItem(originKey(scope,kind),JSON.stringify({[`${kind}_id`]:id,image_id:imageId||null,...(filePath?{file_path:filePath}:{}),...filters}));}
export function consumeReviewContext(storage:Storage,scope:string,ids:string[],kind:'evaluation'|'comparison'='evaluation'):ReviewOriginContext|null{
  const key=originKey(scope,kind);const raw=storage.getItem(key);if(!raw)return null;
  try{const saved=JSON.parse(raw);if(ids.includes(saved[`${kind}_id`])){storage.removeItem(key);return saved;}}catch{storage.removeItem(key);}return null;
}
export function consumeReviewOrigin(storage:Storage,scope:string,ids:string[]):string{
  return consumeReviewContext(storage,scope,ids)?.evaluation_id||'';
}
export function evaluationOriginScope(projectId:string|undefined,source:string,task:string,labelsetId:string,transport?:{transportRevision?:number;selectedProfileId?:string|null;apiTransportIdentity?:string}):string{return JSON.stringify([projectId,source,task,labelsetId,transport?.apiTransportIdentity||'local',transport?.selectedProfileId||'local']);}
