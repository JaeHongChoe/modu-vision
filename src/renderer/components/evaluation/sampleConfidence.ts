/** How far a test accuracy can be trusted with this many test images (audit F-10 / B1).
 *  The Wilson score interval stays honest at small counts and at 0% or 100%, where "100% on 4 images" otherwise
 *  reads like a guarantee. Pure; the numbers come from the evaluation already on screen. */
import { RELIABLE_TEST_IMAGES } from '../dataset/importChecklist';

/** 95% Wilson interval for successes/total as fractions, or null without images. */
export function wilsonInterval(successes: number, total: number, z = 1.96): { low: number; high: number } | null {
  if (!Number.isFinite(total) || total <= 0 || successes < 0 || successes > total) return null;
  const p = successes / total, z2 = z * z;
  const centre = (p + z2 / (2 * total)) / (1 + z2 / total);
  const margin = (z / (1 + z2 / total)) * Math.sqrt(p * (1 - p) / total + z2 / (4 * total * total));
  return { low: Math.max(0, centre - margin), high: Math.min(1, centre + margin) };
}

/** "정확도 100% · 실제는 51~100% 사이일 수 있음 (시험 4장)", or the interval alone once the test set is large enough. */
export function accuracyNote(right: number, total: number): string {
  const interval = wilsonInterval(right, total);
  if (!interval) return '정확도 -';
  const pct = (value: number) => Math.round(value * 100);
  const range = `${pct(interval.low)}~${pct(interval.high)}%`;
  return total < RELIABLE_TEST_IMAGES
    ? `정확도 ${pct(right / total)}% · 실제는 ${range} 사이일 수 있음 (시험 ${total}장)`
    : `정확도 ${pct(right / total)}% (95% 신뢰 범위 ${range})`;
}

/** A warning for the recommended threshold and accuracy when the test set is small, or null. */
export function smallTestWarning(total: number): string | null {
  if (total <= 0 || total >= RELIABLE_TEST_IMAGES) return null;
  return `시험 이미지가 ${total}장뿐이라 한 장마다 정확도가 ${Math.round(100 / total)}%p씩 움직이고, 추천 점수도 크게 흔들릴 수 있습니다. `
    + `${RELIABLE_TEST_IMAGES}장 이상으로 평가하면 믿을 만해집니다.`;
}
