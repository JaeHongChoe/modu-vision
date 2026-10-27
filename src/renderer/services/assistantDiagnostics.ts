/** Provider-safe diagnostic primitives. Never stringify an arbitrary error/result object. */
const basename = (value: string) => value.split(/[\\/]+/).filter(Boolean).pop() || '[경로]';
const firstText = (row: Record<string, unknown>, keys: string[]) => keys.map(key => row[key]).find(value => typeof value === 'string' && value.trim());

/** Text is untrusted evidence; remove authority/credential material before applying the output bound. */
export function diagnosticText(value: unknown, limit = 240): string | null {
  if (typeof value !== 'string' || !value.trim()) return null;
  const bound = Math.max(1, Math.min(2000, Number.isFinite(limit) ? Math.floor(limit) : 240));
  let text = value
    .replace(/\b(?:authorization|proxy-authorization|set-cookie|cookie)["']?\s*[:=]\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\r\n]*(?:\r?\n[ \t]+[^\r\n]*)*)/gi, '[자격증명]')
    .replace(/\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|cookie|set-cookie|csrf)["']?\s*[:=]\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}]+)/gi, '[자격증명]')
    .replace(/\bBearer\s+\S+/gi, '[자격증명]')
    .replace(/\bsk-[A-Za-z0-9_-]{8,}/g, '[자격증명]')
    .replace(/\b[a-z][a-z0-9+.-]*:\/\/[^\s"'<>)]*/gi, '[주소]')
    .replace(/(^|[\s"'(\[=:,])((?:[A-Za-z]:[\\/]|\\\\|~[\\/])[^\s"'<>|?*]*)/g, (_m, lead: string, path: string) => lead + basename(path))
    .replace(/(^|[\s"'(\[=:,])(\/(?:[^\s/"'()\[\],<>]+\/)+[^\s/"'()\[\],<>]*)/g, (_m, lead: string, path: string) => lead + basename(path))
    .replace(/\b[\w.+-]+@[A-Za-z0-9_.-]+(?::\d+)?\b/g, '[주소]')
    .replace(/\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b/g, '[주소]')
    .replace(/\[[0-9a-f:.%]+\](?::\d{1,5})?/gi, '[주소]')
    .replace(/\b[a-z][a-z0-9_-]*:\d{1,5}\b/gi, '[주소]')
    .replace(/\b(?:localhost|[a-z0-9-]+(?:\.[a-z0-9-]+)+)(?::\d{1,5})?\b/gi, match => /\.(?:png|jpe?g|bmp|tiff?|webp|pt|pth|onnx|json|csv|txt|yaml|yml|xml|safetensors)$/i.test(match) ? match : '[주소]')
    .replace(/[A-Za-z0-9+/=_-]{40,}/g, '[가림]')
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')
    .trim();
  if (text.length > bound) text = text.slice(0, bound - 1) + '…';
  return text || null;
}

export interface SafeDiagnosticError {
  code: string | null; message: string; status: number | null;
  severity?: 'critical' | 'high' | 'medium' | 'low';
  cause?: string;
  /** Displayed recovery guidance only. This never authorizes an automatic action. */
  suggested_action?: string;
}
export function diagnosticError(cause: unknown): SafeDiagnosticError {
  const outer = cause && typeof cause === 'object' && !Array.isArray(cause) ? cause as Record<string, unknown> : {};
  const detail = outer.detail && typeof outer.detail === 'object' && !Array.isArray(outer.detail) ? outer.detail as Record<string, unknown> : {};
  const status = [outer.status, detail.status].find(value => typeof value === 'number' && Number.isInteger(value) && value >= 100 && value <= 599);
  const rawCode = firstText(outer, ['error_code', 'code']) || firstText(detail, ['error_code', 'code']);
  // Dotted codes have registered semantic prefixes; an arbitrary dotted hostname is not an error code.
  const validCode = typeof rawCode === 'string' && /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/.test(rawCode)
    && (!rawCode.includes('.') || /^(?:ERR|training|dataset|evaluation|flow|inspection|compute|assistant|context|auth|http|network|request|error)\.[A-Za-z0-9_.-]+$/.test(rawCode));
  const code = validCode ? rawCode as string : null;
  const read = (keys: string[]) => firstText(outer, keys) || firstText(detail, keys);
  const rawMessage = typeof cause === 'string' ? cause : read(['message_ko', 'message_kr', 'description_kr', 'message', 'reason', 'description_en', 'message_en', 'detail']);
  const severity = [outer.severity, detail.severity].find(value => typeof value === 'string' && ['critical', 'high', 'medium', 'low'].includes(value)) as SafeDiagnosticError['severity'];
  const knownCause = diagnosticText(read(['cause_kr', 'cause_ko', 'cause_en', 'cause']), 180);
  const recovery = diagnosticText(read(['remediation_kr', 'remediation_ko', 'remediation', 'remediation_en', 'suggested_action', 'action']), 200);
  return {code, message: diagnosticText(rawMessage, 360) || '오류 설명을 확인하지 못했습니다.', status: typeof status === 'number' ? status : null,
    ...(severity ? {severity} : {}), ...(knownCause ? {cause: knownCause} : {}), ...(recovery ? {suggested_action: recovery} : {})};
}
