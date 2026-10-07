import {safeSnapshot} from '../common/evidenceViewer';

/** Persisted result bytes cannot introduce an external image origin. */
export function inspectionThumbnail(value:string|undefined):string|undefined {
  if(typeof value!=='string'||!value)return undefined;
  if(safeSnapshot(value))return value;
  if(!value.startsWith('/api/dataset/thumbnail/')||/[\\\u0000-\u001f#]/.test(value.split('?')[0]))return undefined;
  const url=new URL(value,'http://owned.invalid');
  return url.pathname.startsWith('/api/dataset/thumbnail/')&&url.pathname.length>'/api/dataset/thumbnail/'.length?value:undefined;
}
export function inspectionPreview(result:string|undefined,thumbnail:string|undefined):string|undefined {
  return result&&safeSnapshot(result)?result:inspectionThumbnail(thumbnail);
}
