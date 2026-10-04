"""E02: a fixture ROI anchored to reference features (OpenCV keypoints, rigid or similarity scope).

Synthetic parts are moved by known rotations and translations; the reference ROI must land on the same source pixels
and a reference mask must come back on the source with its labels. Each REVIEW gate is checked on its own: too few
features, too few or clustered inliers (a small visible patch, a low-texture part), weak support, a residual, a second
position (two copies, also rotated about the image origin), the rigid scope and the placement bound. A new reference
revision makes earlier evidence stale, and the same input always gives the same pose.
"""
import cv2
import numpy as np
import pytest

from backend.engine.fixture_pose import (FixtureLimits, FixtureReference, locate_fixture, mask_to_observed,
                                         pose_is_current, reference_points_to_observed, roi_in_observed)

FULL = (0, 0, 320, 240)
ROI = (60.0, 50.0, 180.0, 140.0)


def fixture_image(seed=7, size=(240, 320)):
    """A part with distinct, non-repeating features (filled shapes and strokes of varied grey) on a plain plate."""
    rng = np.random.RandomState(seed)
    image = np.full(size, 70, np.uint8)
    for _ in range(40):
        x, y = int(rng.randint(10, size[1] - 40)), int(rng.randint(10, size[0] - 40))
        shade = int(rng.randint(120, 250))
        if rng.rand() < 0.5:
            cv2.rectangle(image, (x, y), (x + int(rng.randint(8, 35)), y + int(rng.randint(8, 35))), shade, -1)
        else:
            cv2.circle(image, (x, y), int(rng.randint(4, 16)), shade, -1)
        cv2.line(image, (x, y), (x + int(rng.randint(-40, 40)), y + int(rng.randint(-40, 40))), int(rng.randint(0, 60)), 2)
    return image


def moved(angle_deg, shift, scale=1.0, centre=(160, 120)):
    matrix = cv2.getRotationMatrix2D(centre, angle_deg, scale)
    matrix[:, 2] += shift
    return matrix


def warp(image, matrix, canvas=(400, 520), background=40, flags=cv2.INTER_LINEAR):
    return cv2.warpAffine(image, matrix, (canvas[1], canvas[0]), flags=flags, borderValue=background)


def corners(box):
    x1, y1, x2, y2 = box
    return np.array([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], float)


def corner_error(pose, truth, box=FULL):
    return np.linalg.norm(reference_points_to_observed(pose, corners(box)) -
                          (corners(box) @ truth[:, :2].T + truth[:, 2]), axis=1).max()


@pytest.mark.parametrize('angle, shift', [(0.0, (90, 70)), (7.0, (110, 60)), (-15.0, (80, 95)), (23.0, (120, 110))])
def test_a_rigidly_moved_part_gets_the_reference_roi_on_the_same_source_pixels(angle, shift):
    reference = FixtureReference(fixture_image(), FULL)
    truth = moved(angle, np.array(shift, float))
    observed = warp(reference.image, truth)
    pose = locate_fixture(reference, observed, scope='rigid')
    assert pose.status == 'located', pose.reason
    assert pose.ambiguity < 0.1 and pose.placement_error_px <= 3.0 and pose.match_quality['distinct_inliers'] >= 8
    assert corner_error(pose, truth) <= pose.placement_error_px, 'the reported bound holds at the region corners'
    assert abs(np.linalg.det(np.array(pose.observed_to_reference_transform)[:, :2]) - 1) < 1e-9, 'a rigid pose has no scale'
    placed = roi_in_observed(pose, ROI)
    expected = corners(ROI) @ truth[:, :2].T + truth[:, 2]
    assert np.abs(np.array(placed['polygon']) - expected).max() < 1.5
    assert np.abs(np.array(placed['bbox']) - [expected[:, 0].min(), expected[:, 1].min(), expected[:, 0].max(), expected[:, 1].max()]).max() < 1.5
    assert placed['inside_frame'] and placed['placement_error_px'] == pose.placement_error_px
    assert corner_error(pose, truth) < 2.0, 'the whole fixture region, not only the ROI'
    # A label mask drawn on the reference comes back on the source where the part really is, with its labels.
    labels = np.zeros((240, 320), np.uint16)
    labels[50:140, 60:180] = 3
    labels[150:200, 200:300] = 700
    restored = mask_to_observed(pose, labels)
    truth_mask = cv2.warpAffine(labels, truth, (observed.shape[1], observed.shape[0]), flags=cv2.INTER_NEAREST)
    assert restored.dtype == np.uint16 and set(np.unique(restored)) == {0, 3, 700}
    for label in (3, 700):
        overlap = np.logical_and(restored == label, truth_mask == label).sum() / np.logical_or(restored == label, truth_mask == label).sum()
        assert overlap > 0.95, (label, overlap)


def test_a_scaled_part_is_outside_the_rigid_scope_and_located_in_the_similarity_scope():
    reference = FixtureReference(fixture_image(), FULL)
    truth = moved(5.0, np.array((70.0, 60.0)), scale=1.12)
    observed = warp(reference.image, truth)
    rigid = locate_fixture(reference, observed, scope='rigid')
    assert rigid.status == 'review' and 'outside the rigid scope' in rigid.reason and abs(rigid.scale - 1.12) < 0.02
    similar = locate_fixture(reference, observed, scope='similarity')
    assert similar.status == 'located', similar.reason
    assert abs(similar.scale - 1.12) < 0.02 and corner_error(similar, truth) < 2.0
    with pytest.raises(ValueError, match='no fixture pose'):
        roi_in_observed(rigid, ROI)
    # A small scale change inside the 3 % band: removing it would move the far corners, so the rigid pose is refused
    # (by its residual or its placement bound) instead of placing ROIs that far off.
    for scale in (0.99, 1.01, 1.02):
        slight = locate_fixture(reference, warp(reference.image, moved(10.0, np.array((100.0, 80.0)), scale=scale)), scope='rigid')
        assert slight.status == 'review' and any(word in slight.reason for word in ('residual', 'placement', 'rigid scope')), (scale, slight.reason)


def test_a_second_copy_of_the_fixture_is_ambiguous_wherever_the_image_origin_is():
    part = fixture_image()
    reference = FixtureReference(part, FULL)
    side_by_side = np.full((300, 760), 40, np.uint8)
    side_by_side[30:270, 30:350] = part
    side_by_side[30:270, 410:730] = part
    pose = locate_fixture(reference, side_by_side)
    assert pose.status == 'review' and 'ambiguous' in pose.reason and pose.ambiguity > 0.3, pose.reason
    # The second copy is the first rotated 40 degrees about the image origin: the two fits share their translation, so
    # positions are compared by where they put the fixture's corners.
    first = np.array([[1.0, 0, 420], [0, 1.0, 60]])
    second = (np.vstack([cv2.getRotationMatrix2D((0.0, 0.0), -40.0, 1.0), [0, 0, 1]]) @ np.vstack([first, [0, 0, 1]]))[:2]
    plate = np.full((1000, 1000), 40, np.uint8)
    for matrix in (first, second):
        foot = warp(np.full_like(part, 255), matrix, (1000, 1000), background=0, flags=cv2.INTER_NEAREST) > 0
        plate[foot] = warp(part, matrix, (1000, 1000), background=0)[foot]
    rotated = locate_fixture(reference, plate)
    assert rotated.status == 'review' and 'ambiguous' in rotated.reason, rotated.reason


def test_a_small_visible_patch_or_a_part_with_few_corners_cannot_place_the_fixture():
    image = fixture_image(seed=0)
    truth = np.array([[1.0, 0, 100], [0, 1.0, 80]])
    observed = warp(image, truth)
    covered = np.full_like(observed, 40)
    covered[250:320, 350:420] = observed[250:320, 350:420]  # only the part's bottom-right 70x70 (6 %) is visible
    patch = locate_fixture(FixtureReference(image, FULL), covered)
    assert patch.status == 'review' and ('placement uncertainty' in patch.reason or 'does not fit' in patch.reason), patch.reason
    notched = np.full((240, 320), 70, np.uint8)
    cv2.rectangle(notched, (80, 70), (240, 170), 200, -1)
    cv2.rectangle(notched, (225, 80), (240, 95), 70, -1)
    for angle in (179.0, 90.0):
        few = locate_fixture(FixtureReference(notched, FULL), warp(notched, moved(angle, np.array((130.5, 95.25))), (420, 560)))
        assert few.status == 'review', (angle, few.reason)
    blank = locate_fixture(FixtureReference(image, FULL), np.full((300, 400), 128, np.uint8))
    assert blank.status == 'review' and blank.reason.startswith('insufficient features')
    assert locate_fixture(FixtureReference(image, FULL), np.zeros((0, 0), np.uint8)).status == 'review'


def test_a_repeated_texture_gives_review_and_each_limit_is_a_gate_of_its_own():
    pattern = np.full((240, 320), 60, np.uint8)
    for y in range(10, 230, 30):
        for x in range(10, 310, 30):
            cv2.rectangle(pattern, (x, y), (x + 12, y + 12), 220, -1)
    periodic = locate_fixture(FixtureReference(pattern, FULL), warp(pattern, moved(0.0, np.array((95.0, 75.0)))))
    assert periodic.status == 'review' and periodic.reason.startswith('insufficient inliers'), 'no single position wins'
    reference = FixtureReference(fixture_image(), FULL)
    observed = warp(reference.image, moved(4.0, np.array((90.0, 70.0))))
    located = locate_fixture(reference, observed)
    parts = located.match_quality['placement_parts']
    gates = {'too few distinct inlier places': FixtureLimits(min_distinct_inliers=5000),
             'weak match': FixtureLimits(min_inlier_ratio=0.999),
             'the part does not fit': FixtureLimits(max_placement_px=parts['model_px'] / 2),
             'placement uncertainty': FixtureLimits(max_placement_px=round((parts['model_px'] + located.placement_error_px) / 2, 4))}
    for reason, limits in gates.items():
        pose = locate_fixture(reference, observed, limits=limits)
        assert pose.status == 'review' and pose.reason.startswith(reason), (reason, pose.reason)
        assert pose.limits == limits.to_json()
    assert locate_fixture(reference, observed).status == 'located'


def test_only_the_fixture_region_is_read():
    """A part on a textured plate: the region is the part, so the plate never anchors the pose, and pixels outside the
    region (even within a descriptor's reach) change nothing."""
    rng = np.random.RandomState(4)
    plate = rng.randint(0, 255, (400, 520)).astype(np.uint8)
    plate = cv2.GaussianBlur(plate, (5, 5), 0)
    part = fixture_image(seed=9)
    reference_image = plate.copy()
    reference_image[100:340, 100:420] = part
    region = (100, 100, 420, 340)
    reference = FixtureReference(reference_image, region)
    observed = plate.copy()
    observed[109:349, 114:434] = part  # the part moved by (14, 9); the plate stayed
    pose = locate_fixture(reference, observed)
    assert pose.status == 'located', pose.reason
    assert np.abs(reference_points_to_observed(pose, [(200.0, 200.0)])[0] - (214.0, 209.0)).max() < 1.5
    assert pose.valid_region[0] in range(110, 118) and pose.valid_region[1] in range(105, 113)
    changed = reference_image.copy()
    changed[:100] = 255 - changed[:100]
    changed[:, :100] = 255 - changed[:, :100]
    assert locate_fixture(FixtureReference(changed, region), observed).to_json() == {**pose.to_json(),
        'reference_artifact_ref': FixtureReference(changed, region).artifact_ref}
    with pytest.raises(ValueError, match='outside the fixture region'):
        roi_in_observed(pose, (20.0, 20.0, 90.0, 90.0))


def test_a_cluttered_large_frame_still_locates_a_fully_visible_part():
    rng = np.random.RandomState(3)
    frame = np.full((2048, 2448), 40, np.uint8)
    for _ in range(3000):
        cv2.circle(frame, (int(rng.randint(0, 2448)), int(rng.randint(0, 2048))), int(rng.randint(3, 20)), int(rng.randint(80, 255)), -1)
    image = fixture_image(seed=5)
    truth = moved(12.0, np.array((1500.0, 900.0)))
    foot = warp(np.full_like(image, 255), truth, (2048, 2448), background=0, flags=cv2.INTER_NEAREST) > 0
    frame[foot] = warp(image, truth, (2048, 2448), background=0)[foot]
    pose = locate_fixture(FixtureReference(image, FULL), frame)
    assert pose.status == 'located', pose.reason
    assert corner_error(pose, truth) <= pose.placement_error_px <= 3.0
    off = locate_fixture(FixtureReference(image, FULL), warp(image, moved(0.0, np.array((230.0, 175.0)))))  # its corner leaves the frame
    assert off.status == 'located' and not roi_in_observed(off, (250.0, 150.0, 320.0, 240.0))['inside_frame'], 'an ROI past the frame is flagged'


def test_a_new_reference_revision_makes_earlier_evidence_stale_and_a_colour_copy_does_not():
    image = fixture_image()
    first = FixtureReference(image, FULL, revision=1)
    pose = locate_fixture(first, warp(image, moved(3.0, np.array((90.0, 70.0)))))
    assert pose_is_current(pose, first)
    assert pose_is_current(pose, FixtureReference(cv2.cvtColor(image, cv2.COLOR_GRAY2BGR), FULL, revision=1)), 'the same picture in colour'
    assert not pose_is_current(pose, FixtureReference(image, FULL, revision=2))
    assert not pose_is_current(pose, FixtureReference(fixture_image(seed=8), FULL, revision=1))
    assert not pose_is_current(pose, FixtureReference(image, (0, 0, 200, 200), revision=1))
    assert pose.provider['method'] and pose.provider['opencv'] == cv2.__version__


def test_the_same_input_gives_the_same_pose_and_leaves_the_opencv_random_state_alone(tmp_path):
    image = fixture_image()
    observed = warp(image, moved(-9.0, np.array((100.0, 80.0))))
    first = locate_fixture(FixtureReference(image, FULL), observed)
    assert locate_fixture(FixtureReference(image, FULL), observed).to_json() == first.to_json()
    path = tmp_path / 'reference.png'
    cv2.imwrite(str(path), image)
    loaded = FixtureReference(cv2.imread(str(path), cv2.IMREAD_GRAYSCALE), FULL)
    assert loaded.artifact_ref == FixtureReference(image, FULL).artifact_ref, 'a lossless file keeps the reference identity'
    assert locate_fixture(loaded, observed).to_json() == first.to_json()
    draws = []
    for call in (False, True):
        cv2.setRNGSeed(5)
        if call:
            locate_fixture(loaded, observed)
        draws.append(cv2.randu(np.zeros(4, np.float32), 0, 1).tolist())
    assert draws[0] == draws[1]


def test_bad_input_and_limits_are_refused_with_value_errors():
    image = fixture_image()
    reference = FixtureReference(image, FULL)
    for bad in (np.zeros((10, 10), np.float32), np.zeros((10, 10, 2), np.uint8), np.zeros(10, np.uint8), None, [[0]]):
        with pytest.raises(ValueError):
            locate_fixture(reference, bad)
    with pytest.raises(ValueError):
        locate_fixture(reference, image, scope='perspective')
    for region in ((0.0, 0, 320, 240), (True, 0, 320, 240), (300, 0, 100, 240), (0, 0, 321, 240), (0, 0, 20, 20), (0, 0, 320), None):
        with pytest.raises(ValueError):
            FixtureReference(image, region)
    assert FixtureReference(image, tuple(np.int64(value) for value in FULL)).valid_region == FULL
    with pytest.raises(ValueError):
        FixtureReference(image, FULL, revision=0)
    with pytest.raises(ValueError, match='8-bit'):
        FixtureReference(image.astype(np.float32), FULL)
    for limits in ({'max_residual_px': -1}, {'min_inliers': 0}, {'min_inlier_ratio': 1.5}, {'features': 2000, 'max_observed_features': 1000},
                   {'max_placement_px': 0}):
        with pytest.raises(ValueError):
            FixtureLimits(**limits)
    pose = locate_fixture(reference, warp(image, moved(2.0, np.array((90.0, 70.0)))))
    with pytest.raises(ValueError, match='reference shape'):
        mask_to_observed(pose, np.zeros((100, 100), np.uint8))
    with pytest.raises(ValueError, match='not supported'):
        mask_to_observed(pose, np.zeros((240, 320), np.int64))
    flags = np.zeros((240, 320), bool)
    flags[50:140, 60:180] = True
    assert mask_to_observed(pose, flags).dtype == np.bool_


def test_the_placement_bound_covers_the_true_error_of_every_located_pose():
    """A seeded sweep of the calibration: rigid and similarity moves, noise, mild blur, gain, a heavier blur and a slight
    perspective. Wherever a pose is located, the true error at the region corners stays within its placement bound (and
    so within 3 px); most full views are located."""
    rng = np.random.RandomState(20261004)
    located = full_views = 0
    for index in range(36):
        seed = int(rng.randint(0, 60))
        image = fixture_image(seed=seed)
        scope = 'similarity' if index % 3 == 0 else 'rigid'
        scale = float(rng.uniform(0.8, 1.25)) if scope == 'similarity' else 1.0
        truth = moved(float(rng.uniform(-180, 180)), np.array((100.0, 80.0)), scale=scale)
        observed = warp(image, truth, (520, 620))
        kind = index % 6
        if kind == 1:
            observed = np.clip(observed + rng.normal(0, 6, observed.shape), 0, 255).astype(np.uint8)
        elif kind == 2:
            observed = cv2.GaussianBlur(observed, (0, 0), 0.8)
        elif kind == 3:
            observed = np.clip(observed * 0.7 + 30, 0, 255).astype(np.uint8)
        elif kind == 4:
            observed = cv2.GaussianBlur(observed, (0, 0), 2.5)
        pose = locate_fixture(FixtureReference(image, FULL), observed, scope=scope)
        if kind in (0, 1, 3):
            full_views += 1
        if pose.status == 'located':
            located += kind in (0, 1, 3)
            error = corner_error(pose, truth)
            assert error <= pose.placement_error_px <= 3.0, (index, scope, kind, error, pose.placement_error_px)
    assert located >= 0.75 * full_views, (located, full_views)
    # A slight perspective (top corners pulled in 4 px): never a silent wrong pose.
    image = fixture_image(seed=7)
    source = np.float32([[0, 0], [320, 0], [320, 240], [0, 240]])
    target = np.float32([[104, 80], [416, 80], [420, 320], [100, 320]])
    keystone = cv2.warpPerspective(image, cv2.getPerspectiveTransform(source, target), (620, 520), borderValue=40)
    for scope in ('rigid', 'similarity'):
        pose = locate_fixture(FixtureReference(image, FULL), keystone, scope=scope)
        if pose.status == 'located':
            placed = reference_points_to_observed(pose, source.astype(float))
            assert np.linalg.norm(placed - target, axis=1).max() <= pose.placement_error_px, (scope, pose.placement_error_px)


def test_one_corner_found_at_several_pyramid_levels_is_one_place():
    from backend.engine.fixture_pose import _places
    # Two keypoints 0.2 px apart across a 3 px cell boundary, a third close by, and one far away: two places.
    reference = np.array([[2.9, 1.0], [3.1, 1.0], [3.0, 2.5], [40.0, 40.0]])
    observed = reference + 100.0
    places, places_observed = _places(reference, observed, (0, 0))
    assert len(places) == 2
    assert np.allclose(places[0], [3.0, 1.5]) and np.allclose(places_observed[0], [103.0, 101.5])
    # The grouping follows the region, not the image origin.
    moved_places, _ = _places(reference + [1.0, 2.0], observed, (1, 2))
    assert np.allclose(moved_places - [1.0, 2.0], places)


def test_a_similarity_scale_outside_the_calibrated_range_is_review():
    """Review of freeze 3 (P2-1): the placement bound was calibrated on similarity scales 0.7-1.4."""
    reference = FixtureReference(fixture_image(seed=11), FULL)
    for scale, located in ((1.3, True), (1.6, False), (0.6, False)):
        truth = moved(30.0, np.array((280.0, 220.0)), scale=scale)
        pose = locate_fixture(reference, warp(reference.image, truth, canvas=(760, 900)), scope='similarity')
        if located:
            assert pose.status == 'located' and corner_error(pose, truth) <= pose.placement_error_px, (scale, pose.reason)
        else:
            assert pose.status == 'review' and 'calibrated similarity range' in pose.reason, (scale, pose.reason)
    with pytest.raises(ValueError):
        FixtureLimits(min_similarity_scale=1.1)
    wide = FixtureLimits(max_similarity_scale=2.0)
    widened = locate_fixture(reference, warp(reference.image, moved(30.0, np.array((280.0, 220.0)), scale=1.6), canvas=(760, 900)),
                             scope='similarity', limits=wide)
    assert 'calibrated similarity range' not in widened.reason, 'a caller can widen the range (then owns its calibration)'


def test_keypoints_come_only_from_the_region_and_a_search_region_has_a_minimum_size():
    from backend.engine.fixture_pose import MIN_SEARCH_PX, _keypoints
    image = fixture_image(seed=3)
    box = (40, 30, 200, 150)
    points, _ = _keypoints(image, box, 1500)
    assert len(points) > 50
    assert (points[:, 0] >= box[0] - 0.5).all() and (points[:, 0] <= box[2] + 0.5).all()
    assert (points[:, 1] >= box[1] - 0.5).all() and (points[:, 1] <= box[3] + 0.5).all(), 'no keypoint from the mirrored border'
    reference = FixtureReference(image, FULL)
    with pytest.raises(ValueError):
        locate_fixture(reference, warp(image, moved(0.0, np.array((40.0, 40.0)))), search_region=(10, 10, 10 + MIN_SEARCH_PX - 1, 200))
    assert MIN_SEARCH_PX == 32


def test_distinct_places_do_not_depend_on_where_the_region_sits_in_the_reference_image():
    image = fixture_image(seed=9)
    truth = moved(-12.0, np.array((100.0, 80.0)))
    observed = warp(image, truth)
    base = locate_fixture(FixtureReference(image, FULL), observed)
    for offset in ((1, 2), (37, 11)):
        bigger = np.full((240 + offset[1] + 9, 320 + offset[0] + 7), 70, np.uint8)
        bigger[offset[1]:offset[1] + 240, offset[0]:offset[0] + 320] = image
        shifted = locate_fixture(FixtureReference(bigger, (offset[0], offset[1], offset[0] + 320, offset[1] + 240)), observed)
        assert shifted.status == base.status == 'located', shifted.reason
        assert shifted.match_quality['distinct_inliers'] == base.match_quality['distinct_inliers'], offset


def _bowed(image, amplitude, angle, canvas=(520, 640), centre=(320.0, 260.0)):
    """The part bowed (each column moved by amplitude x sin(pi x / width)), then turned and moved: the observed image
    and where each reference point truly lands."""
    height, width = image.shape
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    matrix[:, 2] += np.array(centre) - (width / 2, height / 2)
    inverse = cv2.invertAffineTransform(matrix)
    ys, xs = np.mgrid[0:canvas[0], 0:canvas[1]].astype(np.float64)
    q = np.stack([xs.ravel(), ys.ravel()], 1) @ inverse[:, :2].T + inverse[:, 2]
    source_x, source_y = q[:, 0], q[:, 1] - amplitude * np.sin(np.pi * q[:, 0] / width)
    observed = cv2.remap(image, source_x.reshape(canvas).astype(np.float32), source_y.reshape(canvas).astype(np.float32),
                         cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=50)

    def lands(points):
        points = np.asarray(points, float)
        bent = np.stack([points[:, 0], points[:, 1] + amplitude * np.sin(np.pi * points[:, 0] / width)], 1)
        return bent @ matrix[:, :2].T + matrix[:, 2]
    return observed, lands


def test_a_bent_part_is_review_or_its_bound_covers_the_bend():
    """Review of freeze 3 (P1-1): a bow bends straight lines, which no homography shows; the curved fit sees it."""
    grid = np.stack(np.meshgrid(np.linspace(0, 320, 9), np.linspace(0, 240, 9)), -1).reshape(-1, 2)
    reasons = []
    for seed, angle, amplitude, scope in ((21, 150.0, 5.0, 'similarity'), (21, 150.0, 5.0, 'rigid'), (7, 30.0, 4.0, 'similarity'),
                                          (11, -60.0, 3.0, 'rigid')):
        reference = FixtureReference(fixture_image(seed=seed), FULL)
        observed, lands = _bowed(reference.image, amplitude, angle)
        pose = locate_fixture(reference, observed, scope=scope)
        if pose.status == 'located':
            error = np.linalg.norm(reference_points_to_observed(pose, grid) - lands(grid), axis=1).max()
            assert error <= pose.placement_error_px <= 3.0, (seed, angle, amplitude, scope, error, pose.placement_error_px)
        else:
            reasons.append(pose.reason)
    assert any('a curved fit' in reason for reason in reasons), reasons


def test_a_half_turn_is_either_bounded_in_each_scope_or_refused():
    """OpenCV builds can choose different inliers; the measured bound, not a fixed verdict, governs this half turn."""
    reference = FixtureReference(fixture_image(seed=7), FULL)
    truth = moved(179.0, np.array((100.0, 80.0)))
    for scope in ('rigid', 'similarity'):
        pose = locate_fixture(reference, warp(reference.image, truth), scope=scope)
        if scope == 'similarity':
            assert pose.status == 'located', pose.to_json()
        if pose.status == 'located':
            assert corner_error(pose, truth) <= pose.placement_error_px <= 3.0, (scope, pose.to_json())
        else:
            assert 'placement uncertainty' in pose.reason, (scope, pose.to_json())
            with pytest.raises(ValueError, match='no fixture pose'):
                roi_in_observed(pose, ROI)


def test_a_pose_reports_how_much_of_the_region_its_matched_places_cover():
    """Review of freeze 3 (P3): a frame showing a quarter of the part can locate it correctly; the coverage says so."""
    image = fixture_image(seed=13)
    truth = moved(10.0, np.array((100.0, 80.0)))
    full = locate_fixture(FixtureReference(image, FULL), warp(image, truth))
    # the hull of the matched places, not of the region: features rarely reach a part's corners
    assert full.status == 'located' and full.match_quality['coverage'] >= 0.4, full.match_quality
    quarter = np.full_like(image, 70)
    quarter[:120, :160] = image[:120, :160]
    seen = locate_fixture(FixtureReference(image, FULL), warp(quarter, truth))
    assert 'coverage' in seen.match_quality and seen.match_quality['coverage'] <= full.match_quality['coverage'] / 2, seen.match_quality


@pytest.mark.parametrize('seed, angle, blur, kernel_angle', [(7647, -127.03, 'gaussian', 0.0), (5486, -83.46, 'motion', 42.65),
                                                             (7644, 148.31, 'gaussian', 0.0), (5681, 158.13, 'motion', 117.87)])
@pytest.mark.parametrize('scope', ['rigid', 'similarity'])
def test_blurred_views_stay_within_their_bound(seed, angle, blur, kernel_angle, scope):
    """Held-out blurred scenes from the freeze-4 calibration: with only the larger of the spread and the model term (or 2
    sigmas, or no spread) these located poses exceed their bound, some by more than 3 px."""
    image = fixture_image(seed=seed)
    matrix = cv2.getRotationMatrix2D((160, 120), angle, 1.0)
    matrix[:, 2] += np.array((320.0, 260.0)) - (160, 120)
    observed = cv2.warpAffine(image, matrix, (640, 520), borderValue=50)
    if blur == 'motion':
        kernel = np.zeros((9, 9), np.float32)
        kernel[4, :] = 1
        kernel = cv2.warpAffine(kernel, cv2.getRotationMatrix2D((4, 4), kernel_angle, 1.0), (9, 9))
        observed = cv2.filter2D(observed, -1, kernel / kernel.sum())
    else:
        observed = cv2.GaussianBlur(observed, (0, 0), 3.0)
    pose = locate_fixture(FixtureReference(image, FULL), observed, scope=scope)
    if pose.status == 'located':
        grid = np.stack(np.meshgrid(np.linspace(0, 320, 9), np.linspace(0, 240, 9)), -1).reshape(-1, 2)
        error = np.linalg.norm(reference_points_to_observed(pose, grid) - (grid @ matrix[:, :2].T + matrix[:, 2]), axis=1).max()
        assert error <= pose.placement_error_px <= 3.0, (error, pose.match_quality['placement_parts'])
    else:
        with pytest.raises(ValueError, match='no fixture pose'):
            roi_in_observed(pose, ROI)


@pytest.mark.parametrize('scope', ['rigid', 'similarity'])
def test_strong_defocus_is_refused_even_with_a_relaxed_placement_limit(scope):
    reference = FixtureReference(fixture_image(seed=7644), FULL)
    truth = cv2.getRotationMatrix2D((160, 120), 148.31, 1.0)
    truth[:, 2] += np.array((320.0, 260.0)) - (160, 120)
    observed = cv2.GaussianBlur(warp(reference.image, truth, (520, 640), background=50), (0, 0), 3.0)
    pose = locate_fixture(reference, observed, scope=scope, limits=FixtureLimits(max_placement_px=100.0))
    assert pose.status == 'review' and pose.reason.startswith('insufficient image detail'), pose.to_json()
    detail = pose.match_quality['image_detail']
    assert detail['valid_pixels'] > 1000
    assert detail['relative_detail'] < pose.limits['min_relative_detail'] == 0.1
    with pytest.raises(ValueError, match='no fixture pose'):
        roi_in_observed(pose, ROI)


@pytest.mark.parametrize('scope', ['rigid', 'similarity'])
def test_directional_detail_loss_is_refused_even_with_perpendicular_edges(scope):
    reference = FixtureReference(fixture_image(seed=5681), FULL)
    truth = cv2.getRotationMatrix2D((160, 120), 158.13, 1.0)
    truth[:, 2] += np.array((320.0, 260.0)) - (160, 120)
    kernel = np.zeros((9, 9), np.float32)
    kernel[4, :] = 1
    kernel = cv2.warpAffine(kernel, cv2.getRotationMatrix2D((4, 4), 117.87, 1.0), (9, 9))
    observed = cv2.filter2D(warp(reference.image, truth, (520, 640), background=50), -1, kernel / kernel.sum())
    pose = locate_fixture(reference, observed, scope=scope, limits=FixtureLimits(max_placement_px=100.0))
    assert pose.status == 'review' and pose.reason.startswith('insufficient image detail'), pose.to_json()
    assert pose.match_quality['image_detail']['relative_detail'] < 0.1
    assert pose.match_quality['image_detail']['method'] == 'hessian_gram_min_retention'
    with pytest.raises(ValueError, match='no fixture pose'):
        roi_in_observed(pose, ROI)


def test_the_relative_detail_limit_is_finite_numeric_and_calibrated():
    for value in (False, True, float('nan'), float('inf'), -0.1, 0, 1.01, '0.1'):
        with pytest.raises(ValueError, match='min_relative_detail'):
            FixtureLimits(min_relative_detail=value)


@pytest.mark.parametrize('scale', [0.8, 1.3])
def test_the_placement_residual_is_in_observed_pixels(scale):
    reference = FixtureReference(fixture_image(seed=11), FULL)
    truth = moved(30.0, np.array((280.0, 220.0)), scale=scale)
    pose = locate_fixture(reference, warp(reference.image, truth, (760, 900)), scope='similarity')
    assert pose.status == 'located', pose.to_json()
    # The residual gate retains reference pixels; the placement estimate, its spread and models use observed pixels.
    assert pose.match_quality['placement_parts']['systematic_px'] == pytest.approx(pose.residual * pose.scale, abs=0.0002)


@pytest.mark.parametrize('scale', [0.8, 1.3])
def test_relative_detail_compares_the_same_resampling_and_ignores_gain_and_surround(scale):
    from backend.engine.fixture_pose import _relative_detail
    image = fixture_image(seed=9)
    region = (40, 30, 280, 210)
    part = np.zeros_like(image)
    part[30:210, 40:280] = image[30:210, 40:280]
    truth = moved(47.0, np.array((280.0, 220.0)), scale=scale)
    observed = warp(part, truth, (760, 900), background=0)
    inverse = cv2.invertAffineTransform(truth)
    exact = _relative_detail(image, observed, inverse, corners(region), region)
    assert exact['valid_pixels'] > 10000
    assert exact['relative_detail'] == pytest.approx(1.0, abs=0.001)
    gained = np.round(observed.astype(float) * 0.7 + 20).astype(np.uint8)
    assert _relative_detail(image, gained, inverse, corners(region), region)['relative_detail'] == pytest.approx(1.0, abs=0.03)
    changed = 255 - image
    changed[30:210, 40:280] = image[30:210, 40:280]
    assert _relative_detail(changed, observed, inverse, corners(region), region) == exact


def test_relative_detail_requires_common_supported_pixels_and_reference_detail():
    from backend.engine.fixture_pose import _relative_detail
    image = fixture_image(seed=9)
    outside = np.array([[1.0, 0, 10000.0], [0, 1.0, 10000.0]])
    unseen = _relative_detail(image, image, outside, corners(FULL), FULL)
    assert unseen['valid_pixels'] == 0 and unseen['relative_detail'] == 0
    flat = np.full_like(image, 70)
    unsupported = _relative_detail(flat, flat, np.array([[1.0, 0, 0], [0, 1.0, 0]]), corners(FULL), FULL)
    assert unsupported['valid_pixels'] > 1000 and unsupported['relative_detail'] == 0


@pytest.mark.parametrize('failure', ['degenerate', 'nonfinite', 'failed_decomposition'])
def test_relative_detail_cannot_establish_a_bound_from_an_unmeasurable_direction(monkeypatch, failure):
    from backend.engine.fixture_pose import _relative_detail
    image = fixture_image(seed=9)
    if failure == 'degenerate':
        image = np.tile((np.arange(320) % 20 < 10).astype(np.uint8) * 150 + 50, (240, 1))
    elif failure == 'nonfinite':
        monkeypatch.setattr(np.linalg, 'eigh', lambda _: (np.array([float('nan'), 1, 1]), np.eye(3)))
    else:
        def unavailable(_):
            raise np.linalg.LinAlgError('no converged directional estimate')
        monkeypatch.setattr(np.linalg, 'eigh', unavailable)
    detail = _relative_detail(image, image, np.array([[1.0, 0, 0], [0, 1.0, 0]]), corners(FULL), FULL)
    assert detail['relative_detail'] == 0 and detail['valid_pixels'] > 1000
    assert np.isfinite(detail['reference_detail']) and np.isfinite(detail['observed_detail'])


def test_a_similarity_range_must_include_the_reference_size():
    with pytest.raises(ValueError, match='include 1'):
        FixtureLimits(max_similarity_scale=0.9)
    with pytest.raises(ValueError, match='include 1'):
        FixtureLimits(min_similarity_scale=0.95, max_similarity_scale=0.99)


def _folded(image, fold_px, hinge_x=256.0, canvas=(520, 640), offset=(160.0, 140.0)):
    """The part with everything right of a hinge folded down by up to fold_px at its edge (a tab bent at a hinge), placed on
    a plate without rotation; returns the observed image and where reference points land."""
    width = image.shape[1]
    yy, xx = np.mgrid[0:canvas[0], 0:canvas[1]].astype(np.float64)
    qx, qy = xx - offset[0], yy - offset[1]
    py = qy - fold_px * np.clip((qx - hinge_x) / (width - hinge_x), 0, None)
    observed = cv2.remap(image, qx.astype(np.float32), py.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=40)

    def lands(points):
        points = np.asarray(points, np.float64)
        return np.stack([points[:, 0] + offset[0], points[:, 1] + fold_px * np.clip((points[:, 0] - hinge_x) / (width - hinge_x), 0, None) + offset[1]], 1)
    return observed, lands


def test_a_bend_in_part_of_the_region_is_not_detected_but_an_roi_on_it_shows_no_support():
    """Review of freeze 4 (P1-1): a tab folded at a hinge drops its features as outliers, so the places left look flat and
    the bound does not cover the tab. The module states the region must be rigid; an ROI on the bent part reports the
    matched places do not support it, and a caller can refuse it."""
    reference = FixtureReference(fixture_image(seed=7), FULL)
    observed, lands = _folded(reference.image, 8.0)
    pose = locate_fixture(reference, observed, scope='similarity')
    assert pose.status == 'located', 'the partial bend is not detected (the documented limitation)'
    tab = np.array([[300.0, 60.0], [310.0, 180.0]])
    assert np.abs(reference_points_to_observed(pose, tab) - lands(tab)).max() > pose.placement_error_px, 'the bound does not cover it'
    on_tab = roi_in_observed(pose, (272, 40, 318, 200))
    on_body = roi_in_observed(pose, (80, 60, 200, 180))
    assert on_tab['matched_support'] < 0.5 < 0.95 < on_body['matched_support'], (on_tab['matched_support'], on_body['matched_support'])
    with pytest.raises(ValueError, match='placed by extrapolation'):
        roi_in_observed(pose, (272, 40, 318, 200), min_support=0.9)
    assert roi_in_observed(pose, (80, 60, 200, 180), min_support=0.9)['matched_support'] == on_body['matched_support']
    assert len(pose.match_quality['matched_hull']) >= 3


def test_coverage_is_the_share_of_the_reference_keypoint_area_the_matches_cover():
    reference = FixtureReference(fixture_image(seed=7), FULL)
    full = locate_fixture(reference, warp(reference.image, moved(20.0, np.array((90.0, 70.0)))), scope='rigid')
    covered = reference.image.copy()
    covered[:, 160:] = 40  # the right half hidden
    half = locate_fixture(FixtureReference(reference.image, FULL), warp(covered, moved(20.0, np.array((90.0, 70.0)))), scope='rigid')
    assert full.status == 'located' and full.match_quality['coverage'] > 0.8, full.match_quality['coverage']
    if half.status == 'located':
        assert half.match_quality['coverage'] < 0.65, half.match_quality['coverage']


def test_the_curvature_test_is_the_f_statistic_of_a_second_order_fit_over_an_affine_one():
    """Review of freeze 4 (P3): the F formula and the threshold edges are pinned."""
    from backend.engine import fixture_pose
    corners = np.array([[0.0, 0.0], [320.0, 0.0], [320.0, 240.0], [0.0, 240.0]])
    grid = np.stack(np.meshgrid(np.linspace(10, 310, 7), np.linspace(10, 230, 6)), -1).reshape(-1, 2)
    rng = np.random.RandomState(3)
    observed = grid + np.array([50.0, 40.0]) + rng.normal(0, 0.3, grid.shape)
    observed[:, 1] += 1.5 * ((grid[:, 0] - 160) / 160) ** 2  # a bow
    _perspective, _curved, f_statistic = fixture_pose._model_disagreement(grid, observed, corners, np.array([[1, 0, 50.0], [0, 1, 40.0]]))
    centre = corners.mean(axis=0)
    unit = (grid - centre) / 160.0
    design = np.column_stack([np.ones(len(unit)), unit[:, 0], unit[:, 1], unit[:, 0] ** 2, unit[:, 0] * unit[:, 1], unit[:, 1] ** 2])
    full_rss = float(((design @ np.linalg.lstsq(design, observed, rcond=None)[0] - observed) ** 2).sum())
    flat_rss = float(((design[:, :3] @ np.linalg.lstsq(design[:, :3], observed, rcond=None)[0] - observed) ** 2).sum())
    expected = ((flat_rss - full_rss) / 6) / (full_rss / (2 * len(grid) - 12))
    assert f_statistic == pytest.approx(expected, rel=1e-9) and f_statistic > 6
    assert fixture_pose._model_term(1.0, 2.5, 6.01) == 2.5, 'just above the threshold the curved fit counts'
    assert fixture_pose._model_term(1.0, 2.5, 5.99) == 1.0, 'just below it does not'
    assert fixture_pose._model_term(3.0, 2.5, 50.0) == 3.0
