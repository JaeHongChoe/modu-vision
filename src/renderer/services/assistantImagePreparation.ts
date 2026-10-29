/** Decode only the selected local file; strip metadata and normalize EXIF orientation before hashing/sending. */
const MAX_PIXELS=24_000_000,MAX_EDGE=2048,MAX_BYTES=5*1024*1024;
export function selectedImageDimensions(bytes:Uint8Array,type:string):[number,number]{
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),ascii=(start:number,end:number)=>String.fromCharCode(...bytes.subarray(start,end));
  let size:[number,number]|undefined;
  if(type==='image/png'&&bytes.length>=24&&ascii(1,4)==='PNG'&&ascii(12,16)==='IHDR')size=[view.getUint32(16),view.getUint32(20)];
  else if(type==='image/jpeg'&&bytes[0]===255&&bytes[1]===216){
    let offset=2;
    while(offset+3<bytes.length){
      if(bytes[offset++]!==255)break;while(bytes[offset]===255)offset++;
      const marker=bytes[offset++];if(marker===217||marker===218)break;if(marker===1||marker>=208&&marker<=215)continue;
      if(offset+2>bytes.length)break;const length=view.getUint16(offset);if(length<2||offset+length>bytes.length)break;
      if([192,193,194,195,197,198,199,201,202,203,205,206,207].includes(marker)&&length>=8){size=[view.getUint16(offset+5),view.getUint16(offset+3)];break;}offset+=length;
    }
  }else if(type==='image/webp'&&bytes.length>=30&&ascii(0,4)==='RIFF'&&ascii(8,12)==='WEBP'){
    const tag=ascii(12,16),u24=(offset:number)=>bytes[offset]+bytes[offset+1]*256+bytes[offset+2]*65536;
    if(tag==='VP8X')size=[1+u24(24),1+u24(27)];
    else if(tag==='VP8 '&&bytes[23]===157&&bytes[24]===1&&bytes[25]===42)size=[view.getUint16(26,true)&16383,view.getUint16(28,true)&16383];
    else if(tag==='VP8L'&&bytes[20]===47){const bits=view.getUint32(21,true);size=[(bits&16383)+1,((bits>>>14)&16383)+1];}
  }
  if(!size||size.some(n=>!Number.isSafeInteger(n)||n<=0))throw new Error('이미지 파일을 해석하지 못했습니다. PNG·JPEG·WebP 파일을 확인하세요.');
  if(size[0]*size[1]>MAX_PIXELS)throw new Error('이미지는 2,400만 화소 이하로 선택하세요.');
  return size;
}
export async function normalizeSelectedImage(file:Blob):Promise<Blob>{
  selectedImageDimensions(new Uint8Array(await file.arrayBuffer()),file.type);
  if(typeof createImageBitmap!=='function')throw new Error('이 환경에서 이미지 방향·크기를 확인할 수 없습니다. 최신 브라우저나 앱을 사용하세요.');
  let bitmap:ImageBitmap;
  try{bitmap=await createImageBitmap(file,{imageOrientation:'from-image'});}catch{throw new Error('이미지를 읽지 못했습니다. 다른 PNG·JPEG·WebP 파일을 선택하세요.');}
  try{
    if(!bitmap.width||!bitmap.height||bitmap.width*bitmap.height>MAX_PIXELS)throw new Error('이미지 크기를 확인하세요. 최대 2,400만 화소까지 지원합니다.');
    const scale=Math.min(1,MAX_EDGE/Math.max(bitmap.width,bitmap.height));let width=Math.max(1,Math.round(bitmap.width*scale)),height=Math.max(1,Math.round(bitmap.height*scale));
    for(let attempt=0;attempt<3;attempt++){
      const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;const ctx=canvas.getContext('2d');if(!ctx)throw new Error('첨부 이미지 공간을 만들지 못했습니다.');ctx.drawImage(bitmap,0,0,width,height);
      const output=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('첨부 이미지를 준비하지 못했습니다.')),file.type,.9));
      if(output.size<=MAX_BYTES)return output;width=Math.max(1,Math.floor(width*.75));height=Math.max(1,Math.floor(height*.75));
    }
    throw new Error('변환한 이미지가 5MB를 넘습니다. 더 작은 이미지를 선택하세요.');
  }finally{bitmap.close();}
}
