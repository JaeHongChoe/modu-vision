"""A fixture ROI anchored to reference features (E02).

A part does not always sit at the same pixels: a fixture ROI declared on a reference image follows the part by finding
the reference's features in the observed image. The provider here is OpenCV keypoint matching (ORB, cross-checked)
with a robust transform fit, its scope stated explicitly: ``rigid`` (rotation and translation; the part's size must
stay within ``max_scale_deviation``) or ``similarity`` (rotation, uniform scale within the calibrated range
``min_similarity_scale``-``max_similarity_scale``, translation). A part seen at an angle (perspective) or bent (a bow, a
sag) beyond the placement bound is refused, not modelled. Images are expected free of lens distortion (corrected upstream
with the camera's calibration): radial distortion of a few pixels is not detected.

The reference declares its fixture region: the part's own area, where anchored ROIs live (never a static surround,
which would anchor to the plate instead of the part). Features are read only from that region (it is mirrored outward
so keypoints reach its edge). Regions of about 160x120 pixels and up locate reliably; smaller ones may be REVIEW,
depending on their texture.

A pose is only used when the match bounds where reference points land: enough inliers at enough distinct places (3 px
apart), most matches on the located part agreeing, a small residual, no second position with similar support (repeated
texture or a second copy), the size inside the scope, other fits that agree with the scope's fit, and a placement bound
within ``max_placement_px`` (3 px). The other fits are a perspective one (a homography) and, when the places show real
curvature (an F test of a second-order fit over an affine one), a curved one; how far they put the region corners from
the scope's fit is the model disagreement. The placement bound is the error to expect at the region's corners: the
inlier residual (keypoint bias shared by all points, which scatter alone does not show), plus the larger of three
standard deviations of the fit over the distinct places and the model disagreement and half the smaller (both measure
how loosely the places pin the corners, and they overlap), plus in the rigid scope how far removing the fitted scale
moves the corners. It was calibrated on seeded sweeps of held-out parts (rigid and similarity moves, partial views,
occlusion, blur, noise, compression, gamma, bends), and the tests keep a seeded sample. It is an estimate, not a
guarantee: on scenes it was not calibrated on (other textures and degradations, 3,000 scenes of an independent review),
2.3 % of located poses had a true corner error above their bound and 0.6 % above 3 px (at most 4.0 px). Strong defocus on
fine, regular textures (dot grids) is the weak case. Otherwise the pose says REVIEW with the reason, and no ROI is
placed.

The fixture region must be a rigid part. Both the curvature test and the fits see only the matched places that agree
with one transform: a bend confined to part of the region (a tab folded at a hinge, a curled or sagging end, an S-shaped
bend) drops that part's features as outliers, so the places left look flat and the bound does not cover the bent part.
Such a bend is not detected; choose a region that does not bend.

A located pose reports its coverage and the hull of its matched places. Coverage is the share of the reference's own
keypoint area (the hull of its keypoints in the region) inside the hull of the matched places: about 1 when the whole
textured part was matched. ``roi_in_observed`` reports how much of an ROI lies inside the matched hull (its support):
an ROI outside it is placed by extrapolation, which a partly bent, occluded or missing part makes wrong. Both are reported;
a caller may refuse ROIs below a minimum support.

Every pose names the reference it was measured against (all its grey pixels, its region and revision: changing pixels
outside the region also makes evidence stale, conservatively), the limits it met and the provider. The same input always
gives the same pose (OpenCV's RANSAC uses its own fixed seed); the observed keypoint budget follows the searched area,
so a different frame size can move a pose slightly.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Literal, Optional

import cv2
import numpy as np

Scope = Literal['rigid', 'similarity']
Box = tuple[int, int, int, int]
PROVIDER = {'method': 'orb_keypoints_partial_affine_ransac', 'opencv': cv2.__version__}
_DISTINCT_PX = 3.0        # ORB finds one corner at several pyramid levels: inliers within 3 px of each other are one place
_SIGMA_FLOOR_PX = 0.5     # keypoint position noise is never assumed below this
_PLACEMENT_SIGMAS = 3.0
_SYSTEMATIC_FACTOR = 1.0  # times the inlier RMS residual: keypoint bias shared by all points (calibrated, see the tests)
_CURVATURE_F = 6.0        # F statistic of the curved fit over an affine one above which the part is taken as bent
_ORB_BORDER_PX = 64       # ORB keeps no keypoint this close to a border at its coarsest level (15 px x 1.2^7 = 54)
MIN_SEARCH_PX = 32
_MASK_TYPES = (np.bool_, np.uint8, np.int8, np.uint16, np.int16, np.int32, np.float32, np.float64)


@dataclass(frozen=True)
class FixtureLimits:
    min_inliers: int = 12
    min_distinct_inliers: int = 8
    min_inlier_ratio: float = 0.4       # of the matches that fall on the located part
    max_residual_px: float = 2.0
    max_placement_px: float = 3.0
    max_scale_deviation: float = 0.01   # rigid scope: |observed size - 1|; the placement bound usually refuses less
    min_similarity_scale: float = 0.7   # similarity scope: the size range the placement bound was calibrated on
    max_similarity_scale: float = 1.4
    max_ambiguity: float = 0.3          # second position's support / the pose's support
    features: int = 1500                # keypoints on the reference region; the observed frame gets as many per area
    max_observed_features: int = 12000

    def __post_init__(self):
        for name in ('min_inliers', 'min_distinct_inliers', 'features', 'max_observed_features'):
            value = getattr(self, name)
            if type(value) is not int or value < 3:
                raise ValueError(f'{name} must be a whole number of at least 3')
        if self.max_observed_features < self.features:
            raise ValueError('max_observed_features must be at least features')
        for name, low, high in (('min_inlier_ratio', 0.0, 1.0), ('max_ambiguity', 0.0, 1.0), ('max_scale_deviation', 0.0, 0.5),
                                ('max_residual_px', 0.0, 100.0), ('max_placement_px', 0.0, 100.0),
                                ('min_similarity_scale', 0.0, 1.0), ('max_similarity_scale', 0.0, 10.0)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low < value <= high:
                raise ValueError(f'{name} must be a number above {low} and at most {high}')
        if not self.min_similarity_scale <= 1.0 <= self.max_similarity_scale:
            raise ValueError('the similarity scale range must include 1')

    def to_json(self) -> dict:
        return asdict(self)


def _grey(image, what: str) -> np.ndarray:
    if not isinstance(image, np.ndarray):
        raise ValueError(f'{what} must be an image array')
    if image.dtype != np.uint8:
        raise ValueError(f'{what} must be 8-bit')
    if image.ndim == 3 and image.shape[2] == 1:
        image = image[:, :, 0]
    if image.ndim == 3 and image.shape[2] in (3, 4) and image.size:
        image = cv2.cvtColor(np.ascontiguousarray(image), cv2.COLOR_BGR2GRAY if image.shape[2] == 3 else cv2.COLOR_BGRA2GRAY)
    if image.ndim != 2:
        raise ValueError(f'{what} must be grey, BGR or BGRA')
    return np.ascontiguousarray(image)


def _box(region, shape, what: str, minimum: int = 32) -> Box:
    if not isinstance(region, (tuple, list)) or len(region) != 4:
        raise ValueError(f'{what} must be four pixel numbers x1, y1, x2, y2')
    if any(isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) for value in region):
        raise ValueError(f'{what} must be four whole pixel numbers')
    x1, y1, x2, y2 = (int(value) for value in region)
    height, width = shape[:2]
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError(f'{what} {[x1, y1, x2, y2]} must lie inside the {width}x{height} image')
    if x2 - x1 < minimum or y2 - y1 < minimum:
        raise ValueError(f'{what} must be at least {minimum}x{minimum} pixels')
    return x1, y1, x2, y2


def _corners(box) -> np.ndarray:
    x1, y1, x2, y2 = box
    return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], np.float64)


def _apply(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    return points @ matrix[:, :2].T + matrix[:, 2]


@dataclass(frozen=True, eq=False)
class FixtureReference:
    """The reference image (grey, BGR or BGRA, 8-bit), its fixture region [x1, y1, x2, y2] in reference pixels and its
    revision."""
    image: np.ndarray
    valid_region: Box
    revision: int = 1
    grey: np.ndarray = field(init=False, repr=False)

    def __post_init__(self):
        grey = _grey(self.image, 'the reference image')
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError('revision must be a whole number of at least 1')
        object.__setattr__(self, 'valid_region', _box(self.valid_region, grey.shape, 'valid_region'))
        object.__setattr__(self, 'grey', grey)

    @property
    def artifact_ref(self) -> str:
        """The identity of what is matched: the grey pixels (a colour copy of the same picture is the same reference),
        their shape, the region and the revision."""
        digest = hashlib.sha256()
        digest.update(self.grey.tobytes())
        digest.update(json.dumps([list(self.grey.shape), self.revision, list(self.valid_region)]).encode())
        return f'fixture-ref:{digest.hexdigest()}'


@dataclass
class FixturePose:
    status: Literal['located', 'review']
    reason: str
    reference_artifact_ref: str
    scope: Scope
    reference_region: Box                     # the fixture region, reference pixels
    reference_shape: tuple[int, int]
    observed_shape: tuple[int, int]
    coordinate_space: str = 'image_pixels'    # both transforms map pixel coordinates (x right, y down)
    observed_to_reference_transform: Optional[list[list[float]]] = None   # 2x3
    reference_to_observed_transform: Optional[list[list[float]]] = None   # 2x3
    match_quality: dict = field(default_factory=dict)  # matches, matches_on_part, inliers, distinct_inliers, inlier_ratio
    residual: Optional[float] = None          # RMS over the inliers, px
    placement_error_px: Optional[float] = None
    ambiguity: Optional[float] = None
    scale: Optional[float] = None             # how large the part appears in the observed image (reference = 1)
    valid_region: Optional[list[int]] = None  # where the pose holds: the fixture region's box in the observed image, clipped
    limits: dict = field(default_factory=dict)
    provider: dict = field(default_factory=lambda: dict(PROVIDER))

    def to_json(self) -> dict:
        return asdict(self)


def _orb(features: int):
    return cv2.ORB_create(nfeatures=features, scaleFactor=1.2, nlevels=8, edgeThreshold=15, patchSize=15, fastThreshold=10)


def _keypoints(grey: np.ndarray, box: Box, features: int):
    """Keypoints of the box only. The box is cropped and mirrored outward by ORB's border, so keypoints are found up to
    its edge while no descriptor reads a pixel outside it."""
    x1, y1, x2, y2 = box
    pad = _ORB_BORDER_PX
    padded = cv2.copyMakeBorder(np.ascontiguousarray(grey[y1:y2, x1:x2]), pad, pad, pad, pad, cv2.BORDER_REFLECT_101)
    mask = np.zeros(padded.shape, np.uint8)
    mask[pad:pad + (y2 - y1), pad:pad + (x2 - x1)] = 255
    points, descriptors = _orb(features).detectAndCompute(padded, mask)
    if descriptors is None or not points:
        return np.zeros((0, 2), np.float32), None
    return np.float32([point.pt for point in points]) + np.float32([x1 - pad, y1 - pad]), descriptors


def _fit(src: np.ndarray, dst: np.ndarray, threshold: float):
    """A similarity transform src -> dst (RANSAC, then refined on the inliers)."""
    matrix, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=threshold,
                                                  maxIters=4000, confidence=0.999, refineIters=20)
    if matrix is None or inliers is None:
        return None, np.zeros(len(src), bool)
    return matrix, inliers.ravel().astype(bool)


def _residual(matrix: np.ndarray, src: np.ndarray, dst: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.sum((_apply(matrix, src) - dst) ** 2, axis=1)))) if len(src) else float('inf')


def _places(reference_points: np.ndarray, observed_points: np.ndarray, origin):
    """One point pair per distinct reference place: inliers within 3 px of a place's first point join it (the place is
    their mean). Membership is by distance between points; the 3 px cells, counted from the region's origin, only find
    the nearby places faster, so where the part sits in the image does not change the grouping."""
    local = reference_points - np.asarray(origin, np.float64)
    buckets: dict[tuple[int, int], list[int]] = {}
    anchors: list[np.ndarray] = []
    group = np.zeros(len(local), np.int64)
    for index, point in enumerate(local):
        cell = (int(point[0] // _DISTINCT_PX), int(point[1] // _DISTINCT_PX))
        found = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for place in buckets.get((cell[0] + dx, cell[1] + dy), ()):
                    if found is None and np.hypot(*(anchors[place] - point)) <= _DISTINCT_PX:
                        found = place
        if found is None:
            found = len(anchors)
            anchors.append(point)
            buckets.setdefault(cell, []).append(found)
        group[index] = found
    count = len(anchors)
    reference = np.zeros((count, 2))
    observed = np.zeros((count, 2))
    np.add.at(reference, group, reference_points)
    np.add.at(observed, group, observed_points)
    sizes = np.bincount(group, minlength=count)[:, None]
    return reference / sizes, observed / sizes


def _placement_sigma(reference: np.ndarray, observed: np.ndarray, corners: np.ndarray) -> float:
    """The largest standard deviation, at the corners, of where a least-squares similarity reference -> observed fitted
    to the distinct places puts a reference point."""
    rows = len(reference)
    design = np.zeros((2 * rows, 4))
    design[0::2] = np.column_stack([reference[:, 0], -reference[:, 1], np.ones(rows), np.zeros(rows)])
    design[1::2] = np.column_stack([reference[:, 1], reference[:, 0], np.zeros(rows), np.ones(rows)])
    target = observed.reshape(-1)
    params, *_ = np.linalg.lstsq(design, target, rcond=None)
    dof = max(1, 2 * rows - 4)
    sigma = max(_SIGMA_FLOOR_PX, float(np.sqrt(np.sum((design @ params - target) ** 2) / dof)))
    covariance = sigma ** 2 * np.linalg.pinv(design.T @ design)
    worst = 0.0
    for x, y in corners:
        jacobian = np.array([[x, -y, 1.0, 0.0], [y, x, 0.0, 1.0]])
        worst = max(worst, float(np.sqrt(np.linalg.eigvalsh(jacobian @ covariance @ jacobian.T).max())))
    return worst


def _hull(points: np.ndarray) -> np.ndarray:
    return cv2.convexHull(np.asarray(points, np.float32).reshape(-1, 1, 2)).reshape(-1, 2) if len(points) >= 3 else np.zeros((0, 2), np.float32)


def _coverage(points: np.ndarray, reference_points: np.ndarray) -> float:
    """The share of the reference's keypoint area (the hull of its keypoints in the region) inside the hull of the matched
    places: about 1 when the whole textured part was matched (reported, not a gate)."""
    matched, whole = _hull(points), _hull(reference_points)
    if len(matched) < 3 or len(whole) < 3:
        return 0.0
    area = float(cv2.contourArea(whole))
    if area <= 0:
        return 0.0
    shared, _ = cv2.intersectConvexConvex(matched.astype(np.float32), whole.astype(np.float32))
    return round(min(1.0, float(shared) / area), 4)


def _quadratic_terms(points: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(len(points)), points[:, 0], points[:, 1], points[:, 0] ** 2, points[:, 0] * points[:, 1],
                            points[:, 1] ** 2])


def _model_disagreement(reference: np.ndarray, observed: np.ndarray, corners: np.ndarray, to_observed: np.ndarray) -> tuple[float, float, float]:
    """How far two other fits to the distinct places put the region corners from the scope's fit: a perspective
    (homography) fit, which sees a part at an angle, and a second-order polynomial fit, which sees a part bent (a bow or
    a sag bends straight lines, which no homography does); and the F statistic of the curved fit over an affine one (how
    much more of the places it explains than keypoint scatter would)."""
    if len(reference) < 8:
        return float('inf'), float('inf'), float('inf')
    scope_corners = _apply(to_observed, corners)
    homography, _ = cv2.findHomography(observed.astype(np.float64), reference.astype(np.float64), 0)
    if homography is None or not np.isfinite(homography).all() or abs(np.linalg.det(homography)) < 1e-12:
        perspective = float('inf')
    else:
        projected = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), np.linalg.inv(homography)).reshape(-1, 2)
        perspective = float(np.linalg.norm(projected - scope_corners, axis=1).max())
    # In coordinates centred on the region and scaled to its half size, so the fit is well conditioned at any position.
    centre = corners.mean(axis=0)
    half = max(1.0, float(np.abs(corners - centre).max()))
    design = _quadratic_terms((reference - centre) / half)
    coefficients, *_ = np.linalg.lstsq(design, observed, rcond=None)
    curved = float(np.linalg.norm(_quadratic_terms((corners - centre) / half) @ coefficients - scope_corners, axis=1).max())
    affine, *_ = np.linalg.lstsq(design[:, :3], observed, rcond=None)
    flat_rss = float(((design[:, :3] @ affine - observed) ** 2).sum())
    curved_rss = float(((design @ coefficients - observed) ** 2).sum())
    freedom = 2 * len(reference) - 12
    f_statistic = ((flat_rss - curved_rss) / 6) / (curved_rss / freedom) if freedom > 0 and curved_rss > 1e-12 else float('inf')
    return perspective, curved, f_statistic


def _model_term(perspective: float, curved: float, f_statistic: float) -> float:
    """The model disagreement: the perspective fit's, and the curved fit's too when the curvature is real (an F statistic
    above _CURVATURE_F: far more than keypoint scatter explains; otherwise extrapolating it would amplify the scatter)."""
    return max(perspective, curved) if f_statistic > _CURVATURE_F else perspective


def locate_fixture(reference: FixtureReference, observed: np.ndarray, *, scope: Scope = 'rigid',
                   limits: FixtureLimits = FixtureLimits(), search_region=None) -> FixturePose:
    """The pose of the reference fixture in the observed image, or REVIEW with the reason. ``search_region`` limits the
    observed pixels searched ([x1, y1, x2, y2]; the whole frame by default)."""
    if scope not in ('rigid', 'similarity'):
        raise ValueError('scope must be rigid or similarity')
    if not isinstance(limits, FixtureLimits):
        raise ValueError('limits must be FixtureLimits')
    obs = _grey(observed, 'the observed image')
    region = reference.valid_region
    corners = _corners(region)
    base = dict(reference_artifact_ref=reference.artifact_ref, scope=scope, reference_region=region,
                reference_shape=tuple(reference.grey.shape), observed_shape=tuple(obs.shape), limits=limits.to_json())

    def review(reason, **extra):
        return FixturePose('review', reason, **base, **extra)
    if obs.size == 0:
        return review('insufficient features: the observed image is empty')
    search = (_box(search_region, obs.shape, 'search_region', minimum=MIN_SEARCH_PX) if search_region is not None
              else (0, 0, obs.shape[1], obs.shape[0]))
    region_area = (region[2] - region[0]) * (region[3] - region[1])
    search_area = (search[2] - search[0]) * (search[3] - search[1])
    observed_features = int(min(limits.max_observed_features, max(limits.features, round(limits.features * search_area / region_area))))
    ref_points, ref_desc = _keypoints(reference.grey, region, limits.features)
    obs_points, obs_desc = _keypoints(obs, search, observed_features)
    if ref_desc is None or obs_desc is None or len(ref_points) < limits.min_inliers or len(obs_points) < limits.min_inliers:
        return review('insufficient features: too few keypoints to locate the fixture')
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(obs_desc, ref_desc)
    if len(matches) < limits.min_inliers:
        return review(f'insufficient features: {len(matches)} matches', match_quality={'matches': len(matches)})
    src = np.float32([obs_points[match.queryIdx] for match in matches])
    dst = np.float32([ref_points[match.trainIdx] for match in matches])
    threshold = limits.max_residual_px * 1.5
    matrix, inliers = _fit(src, dst, threshold)
    count = int(inliers.sum())
    quality = {'matches': len(matches), 'inliers': count}
    if matrix is None or count < limits.min_inliers:
        return review(f'insufficient inliers: {count} of {len(matches)}', match_quality=quality)
    to_observed = cv2.invertAffineTransform(matrix)
    # The ratio counts the matches that fall on the located part, so clutter elsewhere in a large frame does not weaken it.
    on_reference = _apply(matrix, src)
    on_part = int(np.sum((on_reference[:, 0] >= region[0]) & (on_reference[:, 0] <= region[2])
                         & (on_reference[:, 1] >= region[1]) & (on_reference[:, 1] <= region[3])))
    place_ref, place_obs = _places(dst[inliers].astype(np.float64), src[inliers].astype(np.float64), region[:2])
    in_region = np.array([point for point in ref_points if region[0] <= point[0] <= region[2] and region[1] <= point[1] <= region[3]], np.float64)
    quality.update(matches_on_part=on_part, distinct_inliers=len(place_ref), inlier_ratio=round(count / max(1, on_part), 4),
                   coverage=_coverage(place_ref, in_region), matched_hull=np.round(_hull(place_ref), 3).tolist())
    residual = _residual(matrix, src[inliers], dst[inliers])
    observed_scale = 1.0 / float(np.sqrt(abs(np.linalg.det(matrix[:, :2]))))
    # A second transform with similar support among the remaining matches puts the fixture somewhere else: the texture
    # repeats or a second copy is in view, so no pose is chosen. Positions are compared by where they put the fixture
    # region's corners, which does not depend on where the image origin is.
    ambiguity = 0.0
    rest = ~inliers
    if rest.sum() >= limits.min_inliers:
        second, second_inliers = _fit(src[rest], dst[rest], threshold)
        if second is not None:
            moved = np.linalg.norm(_apply(cv2.invertAffineTransform(second), corners) - _apply(to_observed, corners), axis=1).max()
            if moved > limits.max_residual_px * 3:
                ambiguity = round(int(second_inliers.sum()) / count, 4)
    extra = dict(match_quality=quality, residual=round(residual, 4), ambiguity=ambiguity, scale=round(observed_scale, 5))
    if len(place_ref) < limits.min_distinct_inliers:
        return review(f'too few distinct inlier places: {len(place_ref)} (at least {limits.min_distinct_inliers})', **extra)
    if quality['inlier_ratio'] < limits.min_inlier_ratio:
        return review(f'weak match: {count} of {on_part} matches on the part agree', **extra)
    if residual > limits.max_residual_px:
        return review(f'residual {residual:.2f} px exceeds {limits.max_residual_px} px', **extra)
    if ambiguity > limits.max_ambiguity:
        return review(f'ambiguous: a second position has {ambiguity:.0%} of the support (repeated texture)', **extra)
    # The placement bound at the region's corners: the fit's spread (3 sigma over the distinct places), keypoint bias
    # shared by all points (the residual), and how far a perspective fit to the same places disagrees (a part not seen
    # flat-on, or features biased toward one side); in the rigid scope also the removed scale.
    spread = _PLACEMENT_SIGMAS * _placement_sigma(place_ref, place_obs, corners)
    systematic = _SYSTEMATIC_FACTOR * residual
    perspective, curved, f_statistic = _model_disagreement(place_ref, place_obs, corners, to_observed)
    model = _model_term(perspective, curved, f_statistic)
    if model > limits.max_placement_px:
        shape = 'a perspective fit' if perspective >= curved else 'a curved fit'
        return review(f'the part does not fit the {scope} scope: {shape} puts the region corners {model:.2f} px away '
                      '(seen at an angle, bent, or not flat)', **extra)
    # The spread and the model disagreement both measure how loosely the places pin the corners and overlap: the larger
    # one counts in full and the smaller one half (calibrated on held-out sweeps, see the tests), on top of the shared
    # keypoint bias.
    placement = systematic + max(spread, model) + 0.5 * min(spread, model)
    parts = {'spread_px': round(spread, 4), 'systematic_px': round(systematic, 4), 'model_px': round(model, 4),
             'perspective_px': round(perspective, 4), 'curved_px': round(curved, 4),
             'curvature_f': round(f_statistic, 3) if np.isfinite(f_statistic) else None}
    if scope == 'similarity' and not limits.min_similarity_scale <= observed_scale <= limits.max_similarity_scale:
        return review(f'scale {observed_scale:.3f} is outside the calibrated similarity range '
                      f'{limits.min_similarity_scale}-{limits.max_similarity_scale}', **extra)
    if scope == 'rigid':
        if abs(observed_scale - 1.0) > limits.max_scale_deviation:
            return review(f'scale {observed_scale:.3f} is outside the rigid scope', **extra)
        # Remove the fitted scale, keeping rotation and translation about the inliers' centroid; the corners move by
        # the removed scale, which the placement bound includes.
        rotation = matrix[:, :2] * observed_scale
        centre_src = src[inliers].mean(axis=0)
        centre_dst = dst[inliers].mean(axis=0)
        rigid = np.hstack([rotation, (centre_dst - centre_src @ rotation.T).reshape(2, 1)])
        rigid_to_observed = cv2.invertAffineTransform(rigid)
        removed = float(np.linalg.norm(_apply(rigid_to_observed, corners) - _apply(to_observed, corners), axis=1).max())
        placement += removed
        parts['rigid_scale_px'] = round(removed, 4)
        residual = _residual(rigid, src[inliers], dst[inliers])
        extra['residual'] = round(residual, 4)
        matrix, to_observed = rigid, rigid_to_observed
        if residual > limits.max_residual_px:
            return review(f'residual {residual:.2f} px exceeds {limits.max_residual_px} px', **extra)
    extra['placement_error_px'] = round(placement, 4)
    quality['placement_parts'] = parts
    if placement > limits.max_placement_px:
        return review(f'placement uncertainty {placement:.2f} px exceeds {limits.max_placement_px} px '
                      '(the matched features do not pin down the whole fixture region)', **extra)
    placed = _apply(to_observed, corners)
    height, width = obs.shape
    valid = [int(np.clip(np.floor(placed[:, 0].min()), 0, width)), int(np.clip(np.floor(placed[:, 1].min()), 0, height)),
             int(np.clip(np.ceil(placed[:, 0].max()), 0, width)), int(np.clip(np.ceil(placed[:, 1].max()), 0, height))]
    return FixturePose('located', 'located', **base, observed_to_reference_transform=matrix.tolist(),
                       reference_to_observed_transform=to_observed.tolist(), valid_region=valid, **extra)


def _require(pose: FixturePose) -> np.ndarray:
    if pose.status != 'located' or pose.reference_to_observed_transform is None:
        raise ValueError(f'no fixture pose: {pose.reason}')
    return np.array(pose.reference_to_observed_transform, np.float64)


def reference_points_to_observed(pose: FixturePose, points) -> np.ndarray:
    matrix = _require(pose)
    return _apply(matrix, np.asarray(points, np.float64).reshape(-1, 2))


def roi_in_observed(pose: FixturePose, roi_bbox: tuple[float, float, float, float], *, min_support: Optional[float] = None) -> dict:
    """A reference ROI [x1, y1, x2, y2] (inside the fixture region) placed in the observed image: its four corners (a
    rotated rectangle), the axis-aligned box around them, whether it lies wholly inside the frame, the pose's placement
    bound (the error to expect at the region's corners, and so within it, for a rigid region) and its support: the share
    of the ROI inside the hull of the matched places (outside it the ROI is placed by extrapolation). With ``min_support``
    an ROI with less support is refused."""
    matrix = _require(pose)
    x1, y1, x2, y2 = (float(value) for value in roi_bbox)
    if not all(np.isfinite([x1, y1, x2, y2])) or not (x1 < x2 and y1 < y2):
        raise ValueError(f'the ROI {[x1, y1, x2, y2]} must be finite with x1 < x2 and y1 < y2')
    rx1, ry1, rx2, ry2 = pose.reference_region
    if not (rx1 <= x1 < x2 <= rx2 and ry1 <= y1 < y2 <= ry2):
        raise ValueError(f'the ROI {[x1, y1, x2, y2]} lies outside the fixture region {list(pose.reference_region)}')
    hull = np.array((pose.match_quality or {}).get('matched_hull') or [], np.float32).reshape(-1, 2)
    roi_area = (x2 - x1) * (y2 - y1)
    support = (round(min(1.0, float(cv2.intersectConvexConvex(_corners((x1, y1, x2, y2)).astype(np.float32), hull)[0]) / roi_area), 4)
               if len(hull) >= 3 else 0.0)
    if min_support is not None and support < min_support:
        raise ValueError(f'the ROI {[x1, y1, x2, y2]} has {support:.0%} of its area inside the matched places (at least '
                         f'{min_support:.0%} needed): it would be placed by extrapolation')
    corners = _apply(matrix, _corners((x1, y1, x2, y2)))
    height, width = pose.observed_shape
    inside = bool((corners[:, 0] >= 0).all() and (corners[:, 0] <= width).all() and (corners[:, 1] >= 0).all() and (corners[:, 1] <= height).all())
    return {'polygon': corners.round(3).tolist(),
            'bbox': [float(corners[:, 0].min()), float(corners[:, 1].min()), float(corners[:, 0].max()), float(corners[:, 1].max())],
            'inside_frame': inside, 'placement_error_px': pose.placement_error_px, 'matched_support': support}


def mask_to_observed(pose: FixturePose, reference_mask: np.ndarray) -> np.ndarray:
    """A single-channel label mask drawn on the reference (the reference's height and width), restored to the observed
    image (nearest neighbour, so labels are kept exactly)."""
    matrix = _require(pose)
    if not isinstance(reference_mask, np.ndarray) or reference_mask.shape != tuple(pose.reference_shape):
        raise ValueError(f'the mask must have the reference shape {tuple(pose.reference_shape)}')
    if reference_mask.dtype.type not in _MASK_TYPES:
        raise ValueError(f'mask labels of type {reference_mask.dtype} are not supported; use bool, 8/16-bit, signed 32-bit integers or floats')
    source = reference_mask.astype(np.uint8) if reference_mask.dtype == np.bool_ else reference_mask
    height, width = pose.observed_shape
    restored = cv2.warpAffine(source, matrix, (width, height), flags=cv2.INTER_NEAREST, borderValue=0)
    return restored.astype(bool) if reference_mask.dtype == np.bool_ else restored


def pose_is_current(pose: FixturePose, reference: FixtureReference) -> bool:
    """Evidence measured against an earlier reference revision (or another image or region) is stale."""
    return pose.reference_artifact_ref == reference.artifact_ref
