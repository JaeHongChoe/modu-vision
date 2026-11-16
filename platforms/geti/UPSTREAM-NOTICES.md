# Geti upstream source notices

- Repository: https://github.com/open-edge-platform/geti
- Release tag: `app/v3.3.0`
- Pinned commit: `ddcf394d550bf1b84ca262888260043af361f5e7`
- Git tree: `0230bda3c9fce38e275a41403500dc8eec445616`
- Source: [`upstream/`](upstream/), full application and library tree.
- Core license: Apache-2.0; the original full text remains at [`upstream/LICENSE`](upstream/LICENSE).
- Original component/model notices remain at [`upstream/THIRD-PARTY-NOTICES.md`](upstream/THIRD-PARTY-NOTICES.md) and within the source tree. Retain them when producing a source or binary distribution.

Copyright and attribution statements in the pinned source remain with their original files. This M0 import does not modify upstream code or adopt Geti/Intel trademarks as the product name. Future patches must identify changed files and retain their original notices.

The Apache-2.0 core classification does not describe every optional model, weight or binary dependency. The pinned README identifies optional Ultralytics models as AGPL-3.0, and the application's CPU/XPU/CUDA extras include that optional training dependency. The initial Modu feasibility profile must explicitly exclude it and automatic model downloads rather than treating an unfiltered extra as the core-only profile. A shipping package needs its actual dependency/weight inventory and notices, not this source-only ledger as a substitute.

No runtime compatibility, model quality, authentication integration, production deployment or end-user acceptance is asserted by this notice.
