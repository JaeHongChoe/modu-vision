# Label Studio geometry attribution

Modu Vision includes the `getAnchorPoint` arithmetic from
[HumanSignal/label-studio](https://github.com/HumanSignal/label-studio), pinned to
commit `8831d8b9eb507ebb3ce29ab0021e494bc1893a16`.

Upstream source:
[web/libs/editor/src/regions/PolygonRegion.jsx](https://github.com/HumanSignal/label-studio/blob/8831d8b9eb507ebb3ce29ab0021e494bc1893a16/web/libs/editor/src/regions/PolygonRegion.jsx).
The upstream [editor LICENSE](https://github.com/HumanSignal/label-studio/blob/8831d8b9eb507ebb3ce29ab0021e494bc1893a16/web/libs/editor/LICENSE)
and [root NOTICE](https://github.com/HumanSignal/label-studio/blob/8831d8b9eb507ebb3ce29ab0021e494bc1893a16/NOTICE)
are included alongside this file as `LICENSE` and `NOTICE`, with their original
bytes preserved. The reused code is licensed under Apache-2.0.

Modifications for Modu Vision on 2026-11-14: extracted `getAnchorPoint` into
`src/renderer/vendor/label-studio/getAnchorPoint.ts`, added a provenance header
and ESM export, and adapted the function signature with TypeScript parameter
and return types. The arithmetic body is unchanged.

The local segment-bound clamp and zero-length guard are implemented separately
in `src/renderer/components/labeling/polygonEditing.ts`; they do not modify the
upstream arithmetic body. The source repository retains pinned source and
SHA-256 provenance under `third_party/label-studio/`. Its offline verification
command is `node third_party/label-studio/verify-provenance.cjs`.
