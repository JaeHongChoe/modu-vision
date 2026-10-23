/** The last gate before text leaves this computer for an assistant provider (docs/product-direction-easy-labeling-agent.md
 * §4.4). The renderer already sends names and counts only; this pass runs in the main process over every outgoing text
 * (the screen context, the person's message, tool results) so a path, server address, e-mail address or credential that
 * slipped into an error message is reduced before it is sent: a path becomes its file name, an address becomes [주소].
 *
 * Dependency-free so the node tests load it alone. */

const basename = (value: string): string => {
  const parts = value.split(/[\\/]+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : '';
};

// Order matters: file URLs keep their file name, other URLs lose everything; paths are reduced before addresses so a
// Windows drive letter is never read as a host.
const RULES: Array<[RegExp, (match: string, ...groups: string[]) => string]> = [
  [/\bfile:\/\/[^\s"'<>)]*/gi, match => basename(decodeURIComponentSafe(match.replace(/^file:\/\/[^/]*/i, '')))],
  [/\b[a-z][a-z0-9+.-]*:\/\/[^\s"'<>)]*/gi, () => '[주소]'],
  [/(^|[\s"'(\[=:,])([A-Za-z]:[\\/][^\s"'<>|?*]*)/g, (_m, lead, value) => lead + basename(value)],
  [/(^|[\s"'(\[=:,])(\\\\[^\s"'<>|?*]+)/g, (_m, lead, value) => lead + basename(value)],
  [/(^|[\s"'(\[=:,])(~[\\/][^\s"'<>]*)/g, (_m, lead, value) => lead + basename(value)],
  [/(^|[\s"'(\[=:,])(\/(?:[^\s/"'()\[\],<>]+\/)+[^\s/"'()\[\],<>]*)/g, (_m, lead, value) => lead + basename(value)],
  [/\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b/g, () => '[이메일]'],
  [/\bBearer\s+\S+/gi, () => 'Bearer [가림]'],
  [/\bsk-[A-Za-z0-9_-]{8,}/g, () => '[가림]'],
  [/\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b/g, () => '[주소]'],
  [/\b[a-z0-9-]+(?:\.[a-z0-9-]+)+:\d{2,5}\b/gi, () => '[주소]'],
  [/[A-Za-z0-9+/=_-]{40,}/g, () => '[가림]'],
];

function decodeURIComponentSafe(value: string): string {
  try { return decodeURIComponent(value); } catch { return value; }
}

export function redactOutgoingText(value: string): string {
  let text = String(value ?? '');
  for (const [pattern, replace] of RULES) text = text.replace(pattern, replace as (substring: string, ...args: string[]) => string);
  return text;
}

/** JSON values with every string redacted (keys are the app's own field names and stay as they are). */
export function redactOutgoingValue<T>(value: T): T {
  if (typeof value === 'string') return redactOutgoingText(value) as T;
  if (Array.isArray(value)) return value.map(item => redactOutgoingValue(item)) as T;
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => [key, redactOutgoingValue(item)])) as T;
  }
  return value;
}
