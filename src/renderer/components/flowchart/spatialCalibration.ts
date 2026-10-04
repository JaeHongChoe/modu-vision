// E03: the project's spatial calibrations as the measurement editor shows them, and the known-length form's input.

export interface CalibrationRecord {
  ref: string;
  method: 'known_length_planar' | 'manual_planar_scale';
  camera_id: string;
  acquisition_config_hash: string;
  source_size: [number, number];
  scales_or_mapping: { mm_per_pixel_x: number; mm_per_pixel_y: number };
  residual: number | null;
  approved_at: string;
  approved_by: string;
  evidence?: { tolerance_mm?: number; segments?: Array<{ segment: number; length_mm: number; measured_mm: number; error_mm: number }> };
}

export interface KnownLengthSegment { points: [[number, number], [number, number]]; length_mm: number }

const METHOD: Record<CalibrationRecord['method'], string> = { known_length_planar: '기준 길이 교정', manual_planar_scale: '수동 교정(검증 안 됨)' };

/** One line per calibration: camera, method, scales, image size and (for known lengths) the residual. */
export function calibrationLabel(row: CalibrationRecord): string {
  const { mm_per_pixel_x: x, mm_per_pixel_y: y } = row.scales_or_mapping;
  const residual = row.residual === null ? '' : ` · 잔차 ${row.residual.toFixed(4)} mm`;
  return `${row.camera_id} · ${METHOD[row.method] ?? row.method} · X ${x.toFixed(4)} / Y ${y.toFixed(4)} mm/px · ${row.source_size.join('×')}${residual}`;
}

/** Why a calibration cannot measure the selected source image, or null. */
export function calibrationSizeIssue(row: CalibrationRecord | undefined, sourceSize: number[] | undefined): string | null {
  if (!row || !sourceSize || sourceSize.length !== 2 || !sourceSize.every(value => value > 0)) return null;
  return row.source_size[0] === sourceSize[0] && row.source_size[1] === sourceSize[1] ? null
    : `이 교정은 ${row.source_size.join('×')} 이미지용입니다. 선택한 원본은 ${sourceSize.join('×')}이라 mm 기준 검사는 미완료(검토)가 됩니다.`;
}

/** The camera settings typed as `key=value` lines (numbers stay numbers): the setup a calibration is bound to. */
export function parseAcquisitionConfig(text: string): { config: Record<string, string | number> | null; error: string | null } {
  const config: Record<string, string | number> = {};
  for (const [index, raw] of text.split(/\r?\n/).entries()) {
    const line = raw.trim();
    if (!line) continue;
    const at = line.indexOf('=');
    const key = at > 0 ? line.slice(0, at).trim() : '';
    const value = at > 0 ? line.slice(at + 1).trim() : '';
    if (!key || !value) return { config: null, error: `${index + 1}번째 줄은 "이름=값" 형식이어야 합니다.` };
    if (key in config) return { config: null, error: `${key}가 두 번 있습니다.` };
    config[key] = /^-?\d+(\.\d+)?$/.test(value) ? Number(value) : value;
  }
  return Object.keys(config).length ? { config, error: null } : { config: null, error: '해상도·렌즈·초점·거리·노출 같은 카메라 설정을 한 줄에 하나씩 입력하세요.' };
}

/** The known-length form's problem, or null when it can be sent. */
export function knownLengthIssue(segments: KnownLengthSegment[], sourceSize: number[] | undefined, toleranceMm: number | null, isotropic: boolean): string | null {
  if (!sourceSize || sourceSize.length !== 2 || !sourceSize.every(value => Number.isInteger(value) && value > 0)) return '교정할 원본 이미지를 먼저 선택하세요.';
  if (toleranceMm === null || !Number.isFinite(toleranceMm) || toleranceMm <= 0) return '허용 오차(mm)를 입력하세요.';
  const needed = isotropic ? 2 : 3;
  if (segments.length < needed) return `기준 길이를 ${needed}개 이상 입력하세요 (교정값 수보다 하나 더, 검증용).`;
  for (const [index, row] of segments.entries()) {
    const [[x1, y1], [x2, y2]] = row.points;
    if (![x1, y1, x2, y2].every(Number.isFinite) || [x1, x2].some(x => x < 0 || x > sourceSize[0]) || [y1, y2].some(y => y < 0 || y > sourceSize[1]))
      return `${index + 1}번 기준선이 원본 이미지 밖에 있습니다.`;
    if (Math.hypot(x2 - x1, y2 - y1) < 10) return `${index + 1}번 기준선은 10 px 이상이어야 합니다.`;
    if (!Number.isFinite(row.length_mm) || row.length_mm <= 0) return `${index + 1}번 기준선의 실제 길이(mm)를 입력하세요.`;
  }
  return null;
}

const LIMITS = ['min_length', 'max_length', 'min_area', 'max_area'];

/** The measurement node's parameters after choosing a calibration artifact (or none). Limits already written keep
 *  their unit (a pixel limit never silently becomes millimetres); with no limits yet, a chosen artifact means mm. Leaving
 *  an artifact keeps mm limits as they are, so the editor flags that they now need a calibration. */
export function withCalibrationRef(params: Record<string, any>, ref: string | null): Record<string, any> {
  const next = { ...params };
  const hasLimits = LIMITS.some(key => params[key] !== undefined);
  const current = params.threshold_unit ?? (params.calibration || params.calibration_ref ? 'mm' : 'px');
  if (ref) {
    next.calibration_ref = ref;
    delete next.calibration;
    next.threshold_unit = hasLimits ? current : 'mm';
  } else {
    delete next.calibration_ref;
    next.threshold_unit = current;
  }
  return next;
}

/** Another unit for the limits: the values written in the old unit are cleared, never reinterpreted. */
export function withThresholdUnit(params: Record<string, any>, unit: 'px' | 'mm'): Record<string, any> {
  const current = params.threshold_unit ?? (params.calibration || params.calibration_ref ? 'mm' : 'px');
  if (unit === current) return { ...params, threshold_unit: unit };
  const next: Record<string, any> = { ...params, threshold_unit: unit };
  for (const key of LIMITS) delete next[key];
  return next;
}
