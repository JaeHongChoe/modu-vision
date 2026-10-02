import type { LibraryImage, LibraryResolution, LibrarySelection } from '../../services/api';

/** The comparison API accepts at most this many fixed test images. */
export const TEST_SET_LIMIT = 20;

/** A fixed test image: kept by identity and digest; `file_path` is where the current revision has it. */
export interface TestImage extends LibrarySelection { file_name: string; file_path: string }
/** A saved image the current revision does not confirm: kept in the saved set, left out of comparisons. */
export interface UnresolvedTestImage extends TestImage { status: Exclude<LibraryResolution['status'], 'found'> }

const REASONS: Record<UnresolvedTestImage['status'], string> = {
  moved: '옮겨짐', changed: '내용 바뀜', unreadable: '읽지 못함', missing: '찾을 수 없음',
};
export const unresolvedReason = (image: UnresolvedTestImage) => REASONS[image.status];

/** Saved entries: identities (current format) and plain paths saved by older versions, which cannot be resolved. */
export function parseSavedTestSet(raw: string | null): { saved: TestImage[]; legacyPaths: number } {
  let parsed: unknown = [];
  try { parsed = JSON.parse(raw || '[]'); } catch { parsed = []; }
  if (!Array.isArray(parsed)) return { saved: [], legacyPaths: 0 };
  const saved = parsed.filter((entry): entry is TestImage => Boolean(entry) && typeof entry === 'object'
    && typeof (entry as TestImage).image_uuid === 'string' && typeof (entry as TestImage).relative_path === 'string')
    .map(({ image_uuid, sha256, relative_path, file_name, file_path }) => ({ image_uuid, sha256, relative_path, file_name, file_path }))
    // an earlier build could save one image twice; it counts once
    .filter((entry, index, all) => all.findIndex((other) => other.image_uuid === entry.image_uuid) === index);
  return { saved: saved.slice(0, TEST_SET_LIMIT), legacyPaths: parsed.filter((entry) => typeof entry === 'string').length };
}

/** The plain paths an older version saved (offered again only in the legacy list, when no revision is accepted). */
export function legacySavedPaths(...raws: Array<string | null>): string[] {
  const paths: string[] = [];
  for (const raw of raws) {
    try {
      const parsed: unknown = JSON.parse(raw || '[]');
      if (Array.isArray(parsed)) for (const entry of parsed) if (typeof entry === 'string' && !paths.includes(entry)) paths.push(entry);
    } catch { /* an unreadable entry offers nothing */ }
  }
  return paths.slice(0, TEST_SET_LIMIT);
}

/** Where the legacy path list is saved, apart from the identity set (so neither overwrites the other). */
export const legacyStorageKey = (storageKey: string) => `${storageKey}:paths`;

/** The legacy list to offer. Once the legacy key exists it is the list the user edits, so a path unchecked there does
 *  not come back from the identity key; before that, the paths an older version saved under the identity key. */
export function offeredLegacyPaths(storage: Pick<Storage, 'getItem'>, storageKey: string): string[] {
  const kept = storage.getItem(legacyStorageKey(storageKey));
  return kept !== null ? legacySavedPaths(kept) : legacySavedPaths(storage.getItem(storageKey));
}

/** Copy the plain paths an older version saved under the identity key to the legacy key before that key is rewritten,
 *  so a later load without an accepted revision can still offer them. A legacy key that already exists is kept. */
export function preserveLegacyPaths(storage: Pick<Storage, 'getItem' | 'setItem'>, storageKey: string, raw: string | null): void {
  const key = legacyStorageKey(storageKey);
  if (storage.getItem(key) !== null) return;
  const paths = legacySavedPaths(raw);
  if (paths.length) storage.setItem(key, JSON.stringify(paths));
}

export function testImageFrom(item: LibraryImage): TestImage {
  return { image_uuid: item.image_uuid, sha256: item.sha256, relative_path: item.relative_path, file_name: item.file_name, file_path: item.file_path };
}

/** Keep what still names the same bytes (at its current path); keep the rest as unresolved with its reason. */
export function applyResolution(saved: TestImage[], results: LibraryResolution[], legacyPaths = 0):
  { kept: TestImage[]; unresolved: UnresolvedTestImage[]; notice: string | null } {
  const kept: TestImage[] = [];
  const unresolved: UnresolvedTestImage[] = [];
  results.forEach((result, index) => {
    if (result.status === 'found' && result.current) kept.push({ ...saved[index], file_path: result.current.file_path });
    else if (result.status !== 'found') unresolved.push({ ...saved[index], status: result.status });
  });
  const counts = Object.entries(REASONS).map(([status, label]) => [label, unresolved.filter((image) => image.status === status).length] as const)
    .filter(([, count]) => count > 0).map(([label, count]) => `${label} ${count}장`);
  const parts = [
    counts.length && `확인이 필요한 ${unresolved.length}장(${counts.join(' · ')})은 비교에서 빠지고 목록에 남습니다. 확인한 뒤 빼거나 다시 선택하세요.`,
    legacyPaths && `이전 버전에서 경로로만 저장된 ${legacyPaths}장은 다시 선택하세요.`,
  ].filter(Boolean);
  return { kept, unresolved, notice: parts.length ? parts.join(' ') : null };
}

/** Add or remove one image; the saved set (usable and unresolved together) never exceeds the limit. */
export function toggleTestImage(current: TestImage[], item: TestImage, unresolvedCount = 0): TestImage[] {
  if (current.some((entry) => entry.image_uuid === item.image_uuid)) return current.filter((entry) => entry.image_uuid !== item.image_uuid);
  return current.length + unresolvedCount >= TEST_SET_LIMIT ? current : [...current, item];
}

/** A pick from the library. Picking again a saved image that needed checking replaces that entry with the confirmed
 *  one (the same image, or the same bytes found at their new place for a moved one), so it is never saved twice. */
export function pickTestImage(current: TestImage[], unresolved: UnresolvedTestImage[], item: TestImage):
  { testSet: TestImage[]; unresolved: UnresolvedTestImage[] } {
  const replaced = (entry: UnresolvedTestImage) => entry.image_uuid === item.image_uuid
    || (entry.status === 'moved' && Boolean(item.sha256) && entry.sha256 === item.sha256);
  const rest = unresolved.filter((entry) => !replaced(entry));
  if (current.some((entry) => entry.image_uuid === item.image_uuid) && rest.length !== unresolved.length) {
    // already usable (an earlier build saved it in both lists): picking it confirms it instead of removing it
    return { testSet: current, unresolved: rest };
  }
  return { testSet: toggleTestImage(current, item, rest.length), unresolved: rest };
}
