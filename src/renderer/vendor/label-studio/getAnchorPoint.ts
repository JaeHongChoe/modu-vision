// Label Studio / HumanSignal, Apache-2.0; see third_party/label-studio/LICENSE and NOTICE.
// Source: web/libs/editor/src/regions/PolygonRegion.jsx:301 at commit
// 8831d8b9eb507ebb3ce29ab0021e494bc1893a16. Full URL and hashes: third_party/label-studio/provenance.json.
// Modified for Modu Vision on 2026-11-14: extracted this function, added this header,
// the ESM export and TypeScript parameter/return types. Arithmetic body unchanged.
export function getAnchorPoint({ flattenedPoints, cursorX, cursorY }: {
  flattenedPoints: [number, number, number, number];
  cursorX: number;
  cursorY: number;
}): [number, number] {
  const [point1X, point1Y, point2X, point2Y] = flattenedPoints;
  const y =
    ((point2X - point1X) * (point2X * point1Y - point1X * point2Y) +
      (point2X - point1X) * (point2Y - point1Y) * cursorX +
      (point2Y - point1Y) * (point2Y - point1Y) * cursorY) /
    ((point2Y - point1Y) * (point2Y - point1Y) + (point2X - point1X) * (point2X - point1X));
  const x =
    cursorX -
    ((point2Y - point1Y) *
      (point2X * point1Y - point1X * point2Y + cursorX * (point2Y - point1Y) - cursorY * (point2X - point1X))) /
      ((point2Y - point1Y) * (point2Y - point1Y) + (point2X - point1X) * (point2X - point1X));

  return [x, y];
}
