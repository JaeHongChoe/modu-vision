/** SVG preserveAspectRatio uses a centered uniform scale, including letterboxing. */
export function fittingPoint(bounds:{left:number;top:number;width:number;height:number},size:readonly[number,number],client:readonly[number,number]):[number,number]|null {
  const [width,height]=size;const scale=Math.min(bounds.width/width,bounds.height/height);
  if(!Number.isFinite(scale)||scale<=0)return null;
  const x=(client[0]-bounds.left-(bounds.width-width*scale)/2)/scale;
  const y=(client[1]-bounds.top-(bounds.height-height*scale)/2)/scale;
  return x>=0&&x<=width&&y>=0&&y<=height?[x,y]:null;
}
