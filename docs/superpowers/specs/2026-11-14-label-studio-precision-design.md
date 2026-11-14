# 모두비전 폴리곤 정밀 편집

## Intent and scope

The user asked to try the researched open-source approach on a separate branch
of `JaeHongChoe/modu-vision`. This first slice adopts the selected Label Studio
geometry code in the existing labeling canvas. It does not introduce a second
annotation server or replace project, labelset, save, review or training owners.

The prior comparison considered a complete Label Studio application, an embedded
whole editor, and selective editing code. Selective reuse was recommended and
is the approach being tried on this branch. It has the smallest data migration
and dependency cost. Other researched libraries remain proposals, not bundled
work in this slice.

## Behavior

- In select mode a polygon vertex can be selected and moved with the existing
  pointer operation. The selected point is visibly distinguished.
- A selected polygon edge can receive a point with Shift-click. Use a bounded
  CSS-pixel hit radius, transform through the current image viewport, and
  project onto the segment including its closing edge.
- A compact Korean point editor offers previous/next point, insertion at the
  next edge's midpoint, removal, and return to whole-shape selection. These
  controls provide alternatives to precision pointer gestures.
- Delete/Backspace removes only the selected point; with no selected point the
  existing whole-annotation deletion remains. A polygon retains at least three
  points. Minimum-size and duplicate-point rejection explain the next action.
- Arrow keys nudge the selected point by 1 image pixel, or 10 with Shift. With
  no selected point preserve existing whole-shape nudging.
- One point insertion/removal/nudge is one existing undo/redo operation.
- Selection is bound to current project, labelset, image and annotation. Image,
  annotation, tool, project or labelset changes and undo/redo clear point state;
  stale selection must never change another image or delete a whole shape.
- Preserve original image coordinates, polygon/points aliases, bounding box,
  stable annotation ID, category, text, direction and machine provenance.
- Mutations go through the existing annotation store; saves retain revision,
  edit lease, review invalidation, mask regeneration and recovery contracts.

## Reused code and architecture

Pin HumanSignal/label-studio commit
`8831d8b9eb507ebb3ce29ab0021e494bc1893a16`. Reuse only the pure
`getAnchorPoint` arithmetic from `web/libs/editor/src/regions/PolygonRegion.jsx`.
Keep Apache-2.0 LICENSE, NOTICE, source URL and modification notice in the repo.
Add a segment clamp and zero-length guard in a separate local wrapper.

`polygonEditing.ts` owns pure selection geometry and immutable annotation
updates. `LabelingCanvas` owns scoped point selection and existing pointer/key
dispatch. A small point-controls component reuses the project workspace tokens
and buttons. Backend/API schemas and persisted formats remain unchanged.

## Validation and boundaries

Use focused RED/GREEN tests for geometry and real event/store integration,
then the renderer suite, type checks and build. Exercise the actual component
in an isolated browser on synthetic data: point insert/delete/nudge, undo/redo,
image switching, saved/reopened geometry, keyboard controls, narrow viewport
and both themes. Preserve prior source-bound evidence and qualify changed
source explicitly rather than claiming historical acceptance.

This branch is an implementation trial. A successful synthetic/browser test
does not establish better field annotation accuracy, all-platform installation
acceptance or a production rollout. Existing installed app, localhost web,
personal projects, model jobs and original QA data are not replaced.
