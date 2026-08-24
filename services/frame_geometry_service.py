"""Multi-frame geometric artwork detection for non chroma-key mockups.

A mockup's artwork areas share three traits that random room geometry does not:
they are convex quadrilaterals, their interior is flat (a blank placeholder),
and they are either ringed by a picture frame (so the same shape is found at
more than one nesting radius) or plainly neutral in colour. Candidates are
gathered from two independent sources -- closed edge contours and flat blobs
walled off by edges -- then grouped per physical frame and filtered on those
traits.
"""

from __future__ import annotations

import cv2
import numpy as np

# A candidate must cover at least this share of the mockup to count as artwork,
# and at most this share so a whole wall or panel is never mistaken for a frame.
MIN_AREA_FRACTION = 0.003
MAX_AREA_FRACTION = 0.70
# contourArea / minAreaRect area: how close to a true rectangle the quad sits.
MIN_RECTANGULARITY = 0.86
MAX_ASPECT_RATIO = 6.5
# Mean per-channel std inside the quad. Blank placeholders sit far below this.
MAX_INTERIOR_TEXTURE = 14.0
# Mean HSV saturation for an unframed candidate to still read as a placeholder.
MAX_PLAIN_SATURATION = 25.0
# A placeholder painted as a flat fill is this uniform, whatever its colour.
# Room surfaces -- a rug, a wall, a countertop -- never are.
FLAT_FILL_TEXTURE = 1.0
# Smallest share of the biggest detected frame that a companion frame may be.
MIN_RELATIVE_AREA = 1.0 / 10.0
# Containment plus size similarity that make two quads border layers of one frame.
SAME_FRAME_OVERLAP = 0.72
SAME_FRAME_AREA_RATIO = 0.45
# Interior sampled at this scale so the frame border never pollutes the stats.
INTERIOR_SHRINK = 0.78
# How far an edge may be pushed out to meet the frame border, and how much the
# image has to change before a pixel counts as the border rather than placeholder.
EDGE_SNAP_RANGE = 26
EDGE_SNAP_MIN_CONTRAST = 8.0


def order_quad(points) -> np.ndarray:
    """Return the four points ordered clockwise from top-left."""
    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    total = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).ravel()
    ordered = np.zeros((4, 2), dtype=np.float32)
    ordered[0] = pts[np.argmin(total)]  # TL
    ordered[2] = pts[np.argmax(total)]  # BR
    ordered[1] = pts[np.argmin(diff)]   # TR
    ordered[3] = pts[np.argmax(diff)]   # BL
    return ordered


def _edge_map(gray: np.ndarray) -> np.ndarray:
    """Union of several Canny passes so both crisp and low-contrast frames survive."""
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    median = float(np.median(blurred))
    boosted = cv2.GaussianBlur(
        cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray), (5, 5), 0
    )
    passes = [
        cv2.Canny(blurred, int(max(0, 0.66 * median)), int(min(255, 1.33 * median))),
        cv2.Canny(blurred, int(max(0, 0.33 * median)), int(min(255, 1.10 * median))),
        cv2.Canny(blurred, 25, 80),
        cv2.Canny(boosted, 40, 120),
    ]
    edges = passes[0]
    for extra in passes[1:]:
        edges = cv2.bitwise_or(edges, extra)
    return cv2.morphologyEx(
        edges,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        iterations=2,
    )


def _quad_from_contour(contour, allow_min_rect: bool = False):
    perimeter = cv2.arcLength(contour, True)
    if perimeter <= 0:
        return None
    for epsilon in (0.01, 0.02, 0.03, 0.045):
        approx = cv2.approxPolyDP(contour, epsilon * perimeter, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return approx.reshape(4, 2).astype(np.float32)
    if not allow_min_rect:
        return None
    rect = cv2.minAreaRect(contour)
    width, height = rect[1]
    if width < 2 or height < 2:
        return None
    if abs(cv2.contourArea(contour)) / (width * height) < 0.88:
        return None
    return cv2.boxPoints(rect).astype(np.float32)


def _contour_quads(edges: np.ndarray) -> list[np.ndarray]:
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    quads = (_quad_from_contour(c) for c in contours)
    return [q for q in quads if q is not None]


def _flat_region_quads(
    image: np.ndarray, edges: np.ndarray, min_area: float, max_area: float
) -> list[np.ndarray]:
    """Blank placeholders read as low-variance blobs fenced in by the frame edges."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    mean = cv2.blur(gray, (9, 9))
    variance = cv2.blur(gray * gray, (9, 9)) - mean * mean
    flat = (np.sqrt(np.maximum(variance, 0)) < 6.0).astype(np.uint8)
    flat[cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1) > 0] = 0
    flat = cv2.morphologyEx(flat, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

    count, labels, stats, _ = cv2.connectedComponentsWithStats(flat, 8)
    quads = []
    for index in range(1, count):
        if not (min_area < stats[index, cv2.CC_STAT_AREA] < max_area):
            continue
        blob = cv2.morphologyEx(
            (labels == index).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8)
        )
        # Give back the rim that the edge barrier and the opening ate, so the
        # quad lands on the real opening instead of a few pixels inside it.
        blob = cv2.dilate(blob, np.ones((7, 7), np.uint8), iterations=1)
        contours, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        hull = cv2.convexHull(max(contours, key=cv2.contourArea))
        quad = _quad_from_contour(hull, allow_min_rect=True)
        if quad is not None:
            quads.append(quad)
    return quads


def _interior_mask(shape: tuple[int, int], quad: np.ndarray) -> np.ndarray:
    centre = quad.mean(axis=0)
    inner = (centre + (quad - centre) * INTERIOR_SHRINK).astype(np.int32)
    mask = np.zeros(shape, dtype=np.uint8)
    cv2.fillPoly(mask, [inner], 255)
    return mask


def _interior_stats(image: np.ndarray, hsv: np.ndarray, quad: np.ndarray) -> tuple[float, float]:
    """Return (mean per-channel std, mean saturation) inside the quad."""
    mask = _interior_mask(image.shape[:2], quad)
    if cv2.countNonZero(mask) < 40:
        return 999.0, 255.0
    _, texture = cv2.meanStdDev(image, mask=mask)
    saturation, _ = cv2.meanStdDev(hsv, mask=mask)
    return float(texture.mean()), float(saturation[1][0])


def _collect_candidates(image: np.ndarray) -> list[dict]:
    height, width = image.shape[:2]
    image_area = float(width * height)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    edges = _edge_map(gray)

    sourced = [("contour", quad) for quad in _contour_quads(edges)]
    sourced += [
        ("flat", quad)
        for quad in _flat_region_quads(
            image, edges, image_area * MIN_AREA_FRACTION, image_area * MAX_AREA_FRACTION
        )
    ]

    candidates = []
    for source, quad in sourced:
        quad[:, 0] = np.clip(quad[:, 0], 0, width - 1)
        quad[:, 1] = np.clip(quad[:, 1], 0, height - 1)
        area = abs(cv2.contourArea(quad))
        if not (image_area * MIN_AREA_FRACTION < area < image_area * MAX_AREA_FRACTION):
            continue
        rect_w, rect_h = cv2.minAreaRect(quad)[1]
        if rect_w < 6 or rect_h < 6:
            continue
        if area / (rect_w * rect_h) < MIN_RECTANGULARITY:
            continue
        if max(rect_w, rect_h) / min(rect_w, rect_h) > MAX_ASPECT_RATIO:
            continue
        texture, saturation = _interior_stats(image, hsv, quad)
        candidates.append(
            {
                "quad": order_quad(quad),
                "area": area,
                "texture": texture,
                "saturation": saturation,
                "source": source,
            }
        )
    return candidates


def _line_intersection(p1, d1, p2, d2):
    """Where two lines, each given as point + direction, cross."""
    denominator = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(denominator) < 1e-9:
        return None
    delta = p2 - p1
    t = (delta[0] * d2[1] - delta[1] * d2[0]) / denominator
    return p1 + d1 * t


def _edge_border_offset(gray: np.ndarray, start, end, normal) -> float:
    """How far outwards the frame border sits from this edge, in pixels.

    Walks the perpendicular from well inside the opening outwards and returns
    the first offset where the image stops looking like the flat placeholder.
    """
    height, width = gray.shape
    samples = []
    for t in np.linspace(0.18, 0.82, 9):
        point = start + (end - start) * t
        row = []
        for offset in range(-EDGE_SNAP_RANGE, EDGE_SNAP_RANGE + 1):
            probe = point + normal * offset
            x, y = int(round(probe[0])), int(round(probe[1]))
            row.append(float(gray[y, x]) if 0 <= x < width and 0 <= y < height else np.nan)
        samples.append(row)
    stack = np.asarray(samples, dtype=np.float64)
    # An edge running along the canvas border has probes that fall outside the
    # image; there is no frame to snap to there.
    if np.isnan(stack).all(axis=0).any():
        return 0.0
    profile = np.nanmedian(stack, axis=0)

    # Reference the flat interior, which is the inner half of the walk.
    inner = profile[: EDGE_SNAP_RANGE // 2]
    if inner.size == 0:
        return 0.0
    reference = float(np.median(inner))
    threshold = max(EDGE_SNAP_MIN_CONTRAST, 2.5 * float(np.std(inner)))

    for index in range(EDGE_SNAP_RANGE // 2, profile.size):
        if abs(profile[index] - reference) > threshold:
            # Stop just short of the border so artwork never sits on top of it.
            return float(index - EDGE_SNAP_RANGE - 1)
    return 0.0


def snap_to_border(gray: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Push each edge of the quad out to the frame opening it belongs to.

    Contour and blob quads both land a few pixels inside the true opening, and
    that gap renders as a rim of bare mockup around the artwork. Each edge is
    moved independently, so perspective survives, then the corners are rebuilt
    by intersecting the moved edges.
    """
    centre = quad.mean(axis=0)
    lines = []
    for index in range(4):
        start, end = quad[index], quad[(index + 1) % 4]
        direction = end - start
        length = float(np.linalg.norm(direction))
        if length < 4:
            return quad
        outward = (start + end) / 2 - centre
        norm = float(np.linalg.norm(outward))
        if norm == 0:
            return quad
        outward = outward / norm
        offset = _edge_border_offset(gray, start, end, outward)
        offset = float(np.clip(offset, 0.0, EDGE_SNAP_RANGE))
        lines.append((start + outward * offset, direction / length))

    snapped = []
    for index in range(4):
        previous = lines[(index - 1) % 4]
        current = lines[index]
        point = _line_intersection(previous[0], previous[1], current[0], current[1])
        if point is None:
            return quad
        snapped.append(point)
    snapped = np.asarray(snapped, dtype=np.float32)

    # A snap that balloons the quad means an edge locked onto the wrong line.
    if cv2.contourArea(snapped) > cv2.contourArea(quad) * 2.2:
        return quad
    return order_quad(snapped)


def _bounds(quad: np.ndarray) -> tuple[float, float, float, float]:
    return quad[:, 0].min(), quad[:, 1].min(), quad[:, 0].max(), quad[:, 1].max()


def _containment(first: np.ndarray, second: np.ndarray) -> float:
    """Intersection over the smaller bounding box."""
    ax0, ay0, ax1, ay1 = _bounds(first)
    bx0, by0, bx1, by1 = _bounds(second)
    overlap_w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    overlap_h = max(0.0, min(ay1, by1) - max(ay0, by0))
    area_a = max(1.0, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1.0, (bx1 - bx0) * (by1 - by0))
    return (overlap_w * overlap_h) / min(area_a, area_b)


def _same_frame(first: dict, second: dict) -> bool:
    """Two quads are border layers of one frame: nested and close in size."""
    if _containment(first["quad"], second["quad"]) < SAME_FRAME_OVERLAP:
        return False
    smaller, larger = sorted((first["area"], second["area"]))
    return smaller / max(1.0, larger) >= SAME_FRAME_AREA_RATIO


def _cluster_by_frame(candidates: list[dict]) -> list[list[dict]]:
    """Group candidates per physical frame, each group ordered outermost first."""
    clusters: list[list[dict]] = []
    for candidate in sorted(candidates, key=lambda c: c["area"], reverse=True):
        for cluster in clusters:
            if any(_same_frame(member, candidate) for member in cluster):
                cluster.append(candidate)
                break
        else:
            clusters.append([candidate])
    return clusters


def find_frames(image: np.ndarray) -> list[dict]:
    """Detect every artwork area in a mockup.

    Returns one entry per frame, ordered top-to-bottom then left-to-right, each
    holding the innermost quad plus every nesting border layer found for it:
    ``{"corners": ndarray(4, 2), "layers": [ndarray(4, 2), ...], ...}``.
    """
    clusters = _cluster_by_frame(_collect_candidates(image))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    frames = []
    for cluster in clusters:
        # The opening is the innermost quad of the cluster whatever found it: a
        # contour can just as easily have traced the frame's outer edge, and
        # preferring it there hands back a quad larger than the opening.
        innermost = cluster[-1]
        corners = snap_to_border(gray, innermost["quad"])
        frames.append(
            {
                "corners": corners,
                "layers": [member["quad"] for member in cluster],
                "area": abs(cv2.contourArea(corners)),
                "texture": innermost["texture"],
                "saturation": innermost["saturation"],
                "framed": len(cluster) > 1,
            }
        )

    # A blank placeholder is flat, and it is trustworthy when a frame rings it
    # or when its colour is neutral enough to be a stand-in rather than decor.
    # A placeholder is flat, and trustworthy when a frame rings it, when it is
    # a dead-flat fill, or when its colour is neutral enough to be a stand-in.
    kept = [
        frame
        for frame in frames
        if frame["texture"] <= MAX_INTERIOR_TEXTURE
        and (
            frame["framed"]
            or frame["texture"] <= FLAT_FILL_TEXTURE
            or frame["saturation"] <= MAX_PLAIN_SATURATION
        )
    ]
    if not kept:
        # Some mockups ship a sample artwork inside the frame, so nothing is
        # flat. The border ring is then the only signal left worth trusting.
        kept = [frame for frame in frames if frame["framed"]]
    # Nothing framed and nothing flat means no artwork area was found. Say so
    # rather than promoting the largest stray quad, which is never a frame.

    # Frames in a set are comparable in size. A quad a fraction of the biggest
    # one is a window pane, a shelf or a door panel, not a companion artwork.
    if kept:
        largest = max(frame["area"] for frame in kept)
        kept = [frame for frame in kept if frame["area"] >= largest * MIN_RELATIVE_AREA]

    kept.sort(key=lambda f: (f["corners"][:, 1].min(), f["corners"][:, 0].min()))
    return kept
