import { useEffect, useState } from 'react';
import { geometryFlowApi } from '../../services/geometryFlowApi';
import { calibrationLabel, calibrationSizeIssue, knownLengthIssue, parseAcquisitionConfig, withCalibrationRef, withThresholdUnit,
  type CalibrationRecord, type KnownLengthSegment } from './spatialCalibration';

const input = 'mt-1 w-full rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5 text-slate-100';
const button = 'rounded border border-slate-600 px-2 py-1 text-xs hover:bg-slate-700 disabled:opacity-40';
type Params = Record<string, any>;
type Draft = { points: [[number, number], [number, number]]; length: string };
const emptySegment = (): Draft => ({ points: [[0, 0], [0, 0]], length: '' });

/** E03: choose one of the project's calibrations for a measurement node, or make one from known lengths on the
 *  selected source image of a planar fixture. The limits' unit is explicit; changing it clears the old values. */
export function SpatialCalibrationPanel({ params, onChange, sourceSize, sourcePreview }: { params: Params; onChange: (params: Params) => void;
  sourceSize?: number[]; sourcePreview?: string }) {
  const [rows, setRows] = useState<CalibrationRecord[] | null>(null);
  const [loadError, setLoadError] = useState('');
  const [camera, setCamera] = useState('');
  const [settings, setSettings] = useState('');
  const [tolerance, setTolerance] = useState('');
  const [isotropic, setIsotropic] = useState(false);
  const [segments, setSegments] = useState<Draft[]>([emptySegment(), emptySegment(), emptySegment()]);
  const [picking, setPicking] = useState<{ index: number; point: 0 | 1 } | null>(null);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ error?: string; segments?: Array<{ segment: number; error_mm: number }> } | null>(null);
  const load = () => geometryFlowApi.calibrations().then(answer => { setRows(answer.calibrations); setLoadError(''); })
    .catch(cause => setLoadError(cause?.message || String(cause)));
  useEffect(() => { void load(); }, []);
  const selected = rows?.find(row => row.ref === params.calibration_ref);
  const sizeIssue = calibrationSizeIssue(selected, sourceSize);
  const unit = params.threshold_unit ?? (params.calibration || params.calibration_ref ? 'mm' : 'px');
  const typed: KnownLengthSegment[] = segments.map(row => ({ points: row.points, length_mm: Number(row.length) }));
  const parsed = parseAcquisitionConfig(settings);
  const toleranceValue = tolerance.trim() === '' ? null : Number(tolerance);
  const formIssue = !camera.trim() ? '카메라 ID를 입력하세요.' : parsed.error || knownLengthIssue(typed, sourceSize, toleranceValue, isotropic);
  const setPoint = (index: number, point: 0 | 1, axis: 0 | 1, value: number) => setSegments(rows => rows.map((row, at) => {
    if (at !== index) return row;
    const points = row.points.map(pair => [...pair]) as Draft['points'];
    points[point][axis] = value;
    return { ...row, points };
  }));
  const create = async () => {
    if (formIssue || !parsed.config || !sourceSize) return;
    setSaving(true); setResult(null);
    try {
      const made = await geometryFlowApi.createKnownLengthCalibration({ camera_id: camera.trim(), acquisition_config: parsed.config,
        source_size: sourceSize, segments: typed, tolerance_mm: toleranceValue as number, isotropic });
      await load();
      onChange(withCalibrationRef(params, made.ref));
    } catch (cause: any) {
      setResult({ error: cause?.message || String(cause), segments: Array.isArray(cause?.segments) ? cause.segments : undefined });
    } finally {
      setSaving(false);
    }
  };
  return <section aria-label="실제 치수 교정" className="space-y-2 rounded border border-slate-600 p-2">
    <label className="block">교정 artifact
      <select aria-label="교정 artifact" value={params.calibration_ref || ''} disabled={!rows} className={input}
        onChange={event => onChange(withCalibrationRef(params, event.target.value || null))}>
        <option value="">사용 안 함</option>
        {(rows || []).map(row => <option key={row.ref} value={row.ref}>{calibrationLabel(row)}</option>)}
        {params.calibration_ref && !selected && rows && <option value={params.calibration_ref}>이 프로젝트에 없는 교정 {String(params.calibration_ref).slice(19, 31)}</option>}
      </select>
    </label>
    {loadError && <p role="alert" className="text-rose-300">교정 목록을 읽지 못했습니다: {loadError}</p>}
    {params.calibration_ref && rows && !selected && <p role="alert" className="text-amber-300">이 교정이 프로젝트에 없어 mm 기준 검사는 미완료(검토)가 됩니다.</p>}
    {sizeIssue && <p role="alert" className="text-amber-300">{sizeIssue}</p>}
    {selected && <p className="text-[10px] text-slate-400">카메라 {selected.camera_id} · 설정 {selected.acquisition_config_hash.slice(7, 19)} · {selected.approved_by} · {selected.approved_at}. 카메라·설정을 아는 실행에서는 그 둘이 다르면 mm를 쓰지 않습니다.</p>}
    <label className="block">측정 기준 단위
      <select aria-label="측정 기준 단위" value={unit} className={input} onChange={event => onChange(withThresholdUnit(params, event.target.value as 'px' | 'mm'))}>
        <option value="px">px (원본 픽셀)</option>
        <option value="mm">mm (교정 필요)</option>
      </select>
    </label>
    <p className="text-[10px] text-slate-400">단위를 바꾸면 이전 단위로 적은 기준값은 지워집니다. mm 기준은 교정이 없거나 맞지 않으면 픽셀로 비교하지 않고 미완료(검토)가 됩니다.</p>
    <details>
      <summary className="cursor-pointer text-teal-200">기준 길이로 새 교정 만들기</summary>
      <div className="mt-2 space-y-2">
        <p className="text-[10px] text-slate-400">선택한 원본 이미지가 평면 기준물(길이를 아는 눈금·지그)을 찍은 것이어야 합니다. 기준선마다 두 점과 실제 길이를 입력하면 X·Y 교정값을 맞추고, 모든 기준선이 허용 오차 안에 들어올 때만 교정을 만듭니다.</p>
        <label className="block">카메라 ID<input aria-label="교정 카메라 ID" value={camera} onChange={event => setCamera(event.target.value)} className={input} /></label>
        <label className="block">카메라 설정 (한 줄에 이름=값)<textarea aria-label="교정 카메라 설정" rows={3} value={settings} onChange={event => setSettings(event.target.value)}
          placeholder={'resolution=1280x960\nlens=16mm\nworking_distance_mm=300'} className={input} /></label>
        <div className="grid grid-cols-2 gap-2">
          <label>허용 오차 (mm)<input aria-label="교정 허용 오차" type="number" min="0" step="0.001" value={tolerance} onChange={event => setTolerance(event.target.value)} className={input} /></label>
          <label className="flex items-end gap-2"><input type="checkbox" checked={isotropic} onChange={event => setIsotropic(event.target.checked)} />X·Y 같은 교정값</label>
        </div>
        {sourcePreview && sourceSize && sourceSize.every(value => value > 0) && picking && <svg role="img" aria-label="원본 이미지에 기준선 점 지정" viewBox={`0 0 ${sourceSize[0]} ${sourceSize[1]}`}
          className="max-h-60 w-full cursor-crosshair rounded border border-slate-600 bg-slate-950" onClick={event => {
            const rect = event.currentTarget.getBoundingClientRect(); const scale = Math.min(rect.width / sourceSize[0], rect.height / sourceSize[1]);
            const x = (event.clientX - rect.left - (rect.width - sourceSize[0] * scale) / 2) / scale, y = (event.clientY - rect.top - (rect.height - sourceSize[1] * scale) / 2) / scale;
            if (x < 0 || y < 0 || x > sourceSize[0] || y > sourceSize[1]) return;
            setPoint(picking.index, picking.point, 0, Math.round(x)); setPoint(picking.index, picking.point, 1, Math.round(y));
            setPicking(picking.point === 0 ? { index: picking.index, point: 1 } : null);
          }}><image href={sourcePreview} width={sourceSize[0]} height={sourceSize[1]} />
          {segments.map((row, index) => <line key={index} x1={row.points[0][0]} y1={row.points[0][1]} x2={row.points[1][0]} y2={row.points[1][1]} stroke="#fbbf24" strokeWidth={Math.max(...sourceSize) / 300} />)}</svg>}
        <ol className="space-y-1">{segments.map((row, index) => <li key={index} className="flex flex-wrap items-center gap-1">
          <span className="w-5 text-slate-400">{index + 1}</span>
          {([0, 1] as const).map(point => ([0, 1] as const).map(axis => <input key={`${point}${axis}`} aria-label={`${index + 1}번 기준선 ${point + 1}점 ${axis ? 'Y' : 'X'}`} type="number"
            value={row.points[point][axis]} onChange={event => setPoint(index, point, axis, Number(event.target.value))} className="w-16 rounded bg-slate-800 p-1" />))}
          <input aria-label={`${index + 1}번 기준선 실제 길이 (mm)`} type="number" min="0" step="0.001" placeholder="mm" value={row.length}
            onChange={event => setSegments(rows => rows.map((item, at) => at === index ? { ...item, length: event.target.value } : item))} className="w-20 rounded bg-slate-800 p-1" />
          <button type="button" className={button} disabled={!sourcePreview} onClick={() => setPicking({ index, point: 0 })}>이미지에서 지정</button>
          <button type="button" className={button} aria-label={`${index + 1}번 기준선 삭제`} disabled={segments.length <= 2} onClick={() => setSegments(rows => rows.filter((_, at) => at !== index))}>×</button>
          {result?.segments?.find(item => item.segment === index + 1) && <span className="text-[10px] text-amber-300">오차 {result.segments.find(item => item.segment === index + 1)!.error_mm.toFixed(4)} mm</span>}
        </li>)}</ol>
        <div className="flex gap-2">
          <button type="button" className={button} disabled={segments.length >= 50} onClick={() => setSegments(rows => [...rows, emptySegment()])}>기준선 추가</button>
          <button type="button" className={button} disabled={Boolean(formIssue) || saving} onClick={() => void create()}>{saving ? '교정 확인 중…' : '교정 만들기'}</button>
        </div>
        {formIssue && <p className="text-[10px] text-slate-400">{formIssue}</p>}
        {result?.error && <p role="alert" className="text-rose-300">교정을 만들지 않았습니다: {result.error}</p>}
      </div>
    </details>
  </section>;
}
