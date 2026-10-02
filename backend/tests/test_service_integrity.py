"""S0-08: the four reproduced core defects, pinned through their production paths in one suite.

Each test below is the existing production-path regression, imported so that one command runs the whole baseline
(``python -m pytest backend/tests/test_service_integrity.py``):

- S0-01, real ``Dataset.__getitem__``: a horizontal flip moves pixels and boxes together (COCO [2, 3, 4, 4] on a
  32 px image becomes [26, 3, 30, 7]), masks stay aligned, rotated boxes are clipped.
- S0-02, score units: a raw distance threshold of 8 is kept by the catalog and the portable package, agrees in direct
  inference and the heatmap API, is not retuned on the test cohort, and a mismatched calibration is refused first.
- S0-03, stale evidence: changed reviewed truth blocks approval, a later release, and an export during CPU parity.
- S0-04, central release actions: rollback rechecks revocation (no remote command for a revoked release) and the
  emergency path keeps every live gate.

The interface half of S0-08 (real clicks: threshold 8 saved, reopened and run; another version's evaluation never
shown as current; a refused task change) is ``scripts/e2e/service-s0-05.spec.ts`` on the S0-09 harness. That spec
clicks the real renderer but answers the model list and the run from fixtures; it stands in for the planned
``service-integrity.spec.ts`` (a recorded deviation). A full backend run executes these tests twice (here and in their
own modules, about a minute); this module is the one-command baseline, not extra coverage.
"""
from backend.tests.test_fleet_emergency_rollback import (  # noqa: F401  (imported tests run here)
    emergency_scope,
    test_emergency_owner_admin_local_success_and_revocation_preserve_live_gates,
)
from backend.tests.test_service_joint_augmentation import (  # noqa: F401
    source,
    test_detection_loader_flips_pixels_and_all_box_coordinates,
    test_loader_rotation_clips_boxes_and_recomputes_area,
    test_segmentation_loader_flip_keeps_multiclass_mask_aligned,
)
from backend.tests.test_service_release_eligibility import (  # noqa: F401
    bound_context,
    test_central_rollback_rechecks_revocation_and_preserves_valid_history,
    test_changed_reviewed_truth_blocks_approval,
    test_export_rechecks_truth_changed_during_real_cpu_parity,
    test_truth_changed_after_approval_blocks_new_release,
)
from backend.tests.test_service_score_contract import (  # noqa: F401
    test_catalog_and_portable_package_keep_distance_default,
    test_incompatible_calibration_is_rejected_before_anomaly_prediction,
    test_saved_distance_threshold_agrees_in_direct_inference_and_heatmap_api,
    test_test_cohort_does_not_retune_saved_anomaly_threshold,
)

# defect -> (production-path module, its regressions); the module is checked so a local stand-in cannot pin a defect.
PINNED = {
    'S0-01': ('backend.tests.test_service_joint_augmentation',
              ('test_detection_loader_flips_pixels_and_all_box_coordinates', 'test_segmentation_loader_flip_keeps_multiclass_mask_aligned',
               'test_loader_rotation_clips_boxes_and_recomputes_area')),
    'S0-02': ('backend.tests.test_service_score_contract',
              ('test_catalog_and_portable_package_keep_distance_default', 'test_saved_distance_threshold_agrees_in_direct_inference_and_heatmap_api',
               'test_test_cohort_does_not_retune_saved_anomaly_threshold', 'test_incompatible_calibration_is_rejected_before_anomaly_prediction')),
    'S0-03': ('backend.tests.test_service_release_eligibility',
              ('test_changed_reviewed_truth_blocks_approval', 'test_truth_changed_after_approval_blocks_new_release',
               'test_export_rechecks_truth_changed_during_real_cpu_parity')),
    'S0-04': ('backend.tests.test_service_release_eligibility', ('test_central_rollback_rechecks_revocation_and_preserves_valid_history',)),
    'S0-04 emergency': ('backend.tests.test_fleet_emergency_rollback',
                        ('test_emergency_owner_admin_local_success_and_revocation_preserve_live_gates',)),
}
# Each defect's acceptance names these paths; fewer regressions than this means one was dropped from the baseline.
MINIMUM = {'S0-01': 3, 'S0-02': 4, 'S0-03': 3, 'S0-04': 1, 'S0-04 emergency': 1}


def test_every_core_defect_is_pinned_by_a_production_path_regression():
    module = globals()
    wrong = [(defect, name) for defect, (origin, names) in PINNED.items() for name in names
             if not callable(module.get(name)) or module[name].__module__ != origin]
    assert wrong == [], wrong
    assert {defect: len(names) for defect, (_origin, names) in PINNED.items()} == MINIMUM
    collected = {name for name, value in module.items() if name.startswith('test_') and callable(value)} - {
        'test_every_core_defect_is_pinned_by_a_production_path_regression'}
    assert collected == {name for _origin, names in PINNED.values() for name in names}, 'every imported regression is pinned'
