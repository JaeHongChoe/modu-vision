const FALLBACK = '요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.';

/** An API rejection always produces a visible alert, even without server detail. */
export function datasetImportError(caught: unknown): string {
  const seen = new Set<object>();
  const text = (value: unknown, depth = 0): string | null => {
    if (typeof value === 'string') return value.trim() || null;
    if (!value || typeof value !== 'object' || depth > 8 || seen.has(value)) return null;
    seen.add(value);
    try {
      if (value instanceof Error) return text(value.message, depth + 1);
      if (Array.isArray(value)) {
        return value.map(entry => text(entry, depth + 1)).filter(Boolean).join(' · ') || null;
      }
      const detail = value as Record<string, unknown>;
      // Catalog errors prefer Korean; FastAPI lists use msg. Blank fields can
      // fall through to another field rather than hide a useful explanation.
      for (const key of ['message_ko', 'message', 'msg', 'detail']) {
        const found = text(detail[key], depth + 1);
        if (found) return found;
      }
      return null;
    } finally { seen.delete(value); }
  };
  return text(caught) || FALLBACK;
}
