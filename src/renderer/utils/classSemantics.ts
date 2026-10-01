/**
 * Renderer port of backend/engine/class_semantics.py (version 1).
 * classSemanticsCases.json is checked by both implementations, so a change to
 * either alias table must update the shared cases. Prefer roles recorded by the
 * backend when a response provides them.
 */
export const CLASS_SEMANTICS_VERSION = 1;

export type ClassRole = 'normal' | 'defect' | 'unknown';
export type ClassRoles = Readonly<Record<string, ClassRole>>;

const CLASS_ROLES: ReadonlySet<string> = new Set(['normal', 'defect', 'unknown']);
// Whole names only: "0", "bg" or "background" inside a longer name mean nothing.
const NORMAL_NAMES: ReadonlySet<string> = new Set([
  '0', 'bg', 'background', 'nodefect', 'nondefect', 'nondefective', 'notdefective', 'defectfree',
]);
const NORMAL_TOKENS: ReadonlySet<string> = new Set(['ok', 'good', 'normal', 'pass', 'passed', '정상', '정상품', '양품', '합격']);
const DEFECT_TOKENS: ReadonlySet<string> = new Set([
  'ng', 'nok', 'defect', 'defective', 'fail', 'failed', '불량', '불량품', '불합격', '비정상', '결함',
]);
const NEGATIONS: ReadonlySet<string> = new Set(['no', 'non', 'not']);

/** Case-, width- and separator-insensitive tokens of a class name. */
export function classNameTokens(name: unknown): string[] {
  const text = String(name).normalize('NFKC').toLowerCase().trim();
  return text.split(/[\s_\-./]+/u).filter(Boolean);
}

export function resolveClassRole(name: unknown, roles?: ClassRoles | null): [ClassRole, 'explicit' | 'alias' | 'conflict' | 'default'] {
  const explicit = roles?.[String(name)];
  if (explicit && CLASS_ROLES.has(explicit)) return [explicit, 'explicit'];
  const tokens = classNameTokens(name);
  if (NORMAL_NAMES.has(tokens.join(''))) return ['normal', 'alias'];
  const found = new Set<ClassRole>();
  let negate = false;
  for (const token of tokens) {
    if (NEGATIONS.has(token)) {
      negate = true;
      continue;
    }
    const role: ClassRole | null = NORMAL_TOKENS.has(token) ? 'normal' : DEFECT_TOKENS.has(token) ? 'defect' : null;
    if (role) found.add(negate ? (role === 'normal' ? 'defect' : 'normal') : role);
    negate = false;
  }
  if (found.size > 1) return ['defect', 'conflict'];
  if (found.size === 1) return [[...found][0], 'alias'];
  return ['defect', 'default'];
}

export function classRole(name: unknown, roles?: ClassRoles | null): ClassRole {
  return resolveClassRole(name, roles)[0];
}

/** True unless the class is normal; unknown classes are never treated as normal. */
export function isDefectClass(name: unknown, roles?: ClassRoles | null): boolean {
  return classRole(name, roles) !== 'normal';
}
