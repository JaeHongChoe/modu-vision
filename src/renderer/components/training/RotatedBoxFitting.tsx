import {useEffect,useRef,useState} from 'react';
import {request} from '../../services/api';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {programButton,programInput} from './ProgramWorkbenchControls';
import {fittingPoint} from './rotatedFittingCoordinates';

type Point=[number,number];
type Mode='center'|'face'|'irregular';
interface Preview {source_size:[number,number];preview_data_url:string;source_sha256:string}
interface Fit {box:{cx:number;cy:number;width:number;height:number;angle_deg:number};polygon:Point[];source_sha256:string}
const instructions:Record<Mode,string>={center:'중심 → 폭 방향의 끝 → 높이 방향의 끝을 클릭하세요.',face:'한 면의 양 끝 → 반대 면의 점을 클릭하세요.',irregular:'객체 외곽을 세 점 이상 클릭한 뒤 회전 박스를 맞추세요.'};

export function RotatedBoxFitting({source,scope,onAppend}:{source:string;scope:string;onAppend:(row:string)=>void}) {
  const images=useDatasetStore(state=>state.images);
  const [image,setImage]=useState('');const [mode,setMode]=useState<Mode>('center');
  const [points,setPoints]=useState<Point[]>([]);const [preview,setPreview]=useState<Preview|null>(null);const [fit,setFit]=useState<Fit|null>(null);
  const [label,setLabel]=useState('defect');const [split,setSplit]=useState('train');const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  const current=useRef(`${scope}/${image}`);current.current=`${scope}/${image}`;
  const mounted=useRef(true);useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{setImage('');setPoints([]);setFit(null);setPreview(null);setError('');},[scope]);
  useEffect(()=>{
    let active=true;setPoints([]);setFit(null);setPreview(null);setError('');setBusy(false);
    if(image)void request<Preview>(`/api/rotated-detection/fit-source?image_path=${encodeURIComponent(image)}`).then(row=>{if(active)setPreview(row);}).catch(cause=>{if(active)setError(cause instanceof Error?cause.message:String(cause));});
    return()=>{active=false;};
  },[scope,image]);
  const fitBox=async()=>{
    const expected=current.current;setBusy(true);setError('');const same=()=>mounted.current&&current.current===expected;
    try {const row=await request<Fit>('/api/rotated-detection/fit-box',{method:'POST',body:JSON.stringify({image_path:image,mode,points,source_sha256:preview?.source_sha256})});if(same())setFit(row);}
    catch(cause){if(same())setError(cause instanceof Error?cause.message:String(cause));}finally{if(same())setBusy(false);}
  };
  const append=()=>{
    if(!fit||!label.trim())return;
    const relative=image.slice(source.replace(/\/+$/,'').length+1);
    if(!relative||relative===image){setError('현재 원본 폴더 안의 이미지를 선택하세요.');return;}
    const box=fit.box;onAppend(`${relative}\t${label.trim()}\t${[box.cx,box.cy,box.width,box.height,box.angle_deg].map(v=>Number(v.toFixed(4))).join(',')}\t${split}`);
    setPoints([]);setFit(null);
  };
  return <section className="space-y-3 rounded border border-[#344255] bg-[#0E1722] p-3">
    <h3 className="font-semibold text-amber-200">원본 이미지에서 회전 박스 그리기</h3>
    <label className="block">이미지<select value={image} onChange={event=>setImage(event.target.value)} className={programInput}><option value="">현재 이미지 목록에서 선택</option>{images.filter(row=>row.file_path.startsWith(`${source.replace(/\/+$/,'')}/`)).map(row=><option value={row.file_path} key={row.image_id}>{row.file_name}</option>)}</select></label>
    <div className="flex flex-wrap gap-2">{(['center','face','irregular'] as Mode[]).map(value=><button type="button" className={`${programButton} ${mode===value?'border-amber-500 text-amber-200':''}`} key={value} onClick={()=>{setMode(value);setPoints([]);setFit(null);}}>{value==='center'?'중심 기준':value==='face'?'한 면 기준':'불규칙 외곽'}</button>)}</div>
    <p className="text-slate-400">{instructions[mode]} 원본 픽셀 좌표로 저장합니다.</p>
    {preview&&<svg role="img" aria-label="회전 박스 정답 그리기" viewBox={`0 0 ${preview.source_size.join(' ')}`} className="max-h-[420px] w-full cursor-crosshair rounded bg-black" onClick={event=>{
      if(busy||(mode!=='irregular'&&points.length>=3))return;
      const point=fittingPoint(event.currentTarget.getBoundingClientRect(),preview.source_size,[event.clientX,event.clientY]);
      if(point){setPoints(old=>[...old,point]);setFit(null);}
    }}>
      <image href={preview.preview_data_url} width={preview.source_size[0]} height={preview.source_size[1]}/>
      <polyline points={points.map(point=>point.join(',')).join(' ')} fill="none" stroke="#fbbf24" strokeWidth={2} vectorEffect="non-scaling-stroke"/>
      {points.map((point,index)=><circle key={index} cx={point[0]} cy={point[1]} r={Math.max(...preview.source_size)/150} fill="#fbbf24"/>)}
      {fit&&<polygon points={fit.polygon.map(point=>point.join(',')).join(' ')} fill="#22d3ee20" stroke="#22d3ee" strokeWidth={2} vectorEffect="non-scaling-stroke"/>}
    </svg>}
    <div className="flex flex-wrap items-end gap-2"><button type="button" className={programButton} disabled={points.length<3||busy} onClick={()=>void fitBox()}>회전 박스 맞추기</button><button type="button" className={programButton} onClick={()=>{setPoints([]);setFit(null);}}>점 다시 그리기</button>
      <label>라벨<input className={programInput} value={label} onChange={event=>setLabel(event.target.value)}/></label><label>데이터 분할<select className={programInput} value={split} onChange={event=>setSplit(event.target.value)}><option value="train">학습</option><option value="val">검증</option><option value="test">시험</option></select></label>
      <button type="button" className={programButton} disabled={!fit||!label.trim()||busy} onClick={append}>정답 표에 추가</button>
    </div>{fit&&<p className="text-cyan-300">중심 ({fit.box.cx.toFixed(1)}, {fit.box.cy.toFixed(1)}) · {fit.box.width.toFixed(1)} × {fit.box.height.toFixed(1)} px · {fit.box.angle_deg.toFixed(1)}°</p>}{error&&<p role="alert" className="text-rose-300">{error}</p>}
  </section>;
}
