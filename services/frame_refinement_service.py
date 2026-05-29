from pathlib import Path
from statistics import median

from PIL import Image, ImageFilter, ImageOps


def _strongest_line(
    pixels,
    *,
    vertical: bool,
    center: int,
    radius: int,
    span_start: int,
    span_end: int,
    boundary: int,
) -> int:
    candidates: list[tuple[int, int]] = []
    start = max(1, center - radius)
    end = min(boundary - 2, center + radius)
    for position in range(start, end + 1):
        if vertical:
            score = sum(pixels[position, offset] for offset in range(span_start, span_end))
        else:
            score = sum(pixels[offset, position] for offset in range(span_start, span_end))
        candidates.append((score, position))
    if not candidates:
        return center
    best_score, best_position = max(candidates)
    typical_score = median(score for score, _position in candidates)
    if typical_score and best_score < typical_score * 1.35:
        return center
    return best_position


def _inner_boundary_line(
    pixels,
    *,
    vertical: bool,
    start: int,
    end: int,
    span_start: int,
    span_end: int,
    boundary: int,
    prefer_high: bool,
) -> int | None:
    candidates: list[tuple[int, int]] = []
    start = max(1, start)
    end = min(boundary - 2, end)
    for position in range(start, end + 1):
        if vertical:
            score = sum(pixels[position, offset] for offset in range(span_start, span_end))
        else:
            score = sum(pixels[offset, position] for offset in range(span_start, span_end))
        candidates.append((score, position))
    if not candidates:
        return None
    # A real opening boundary carries contrast through much of its perpendicular span.
    minimum_boundary_score = max(1200, (span_end - span_start) * 10)
    strongest_score = max(score for score, _position in candidates)
    qualified_threshold = max(minimum_boundary_score, round(strongest_score * 0.45))
    qualified = sorted(
        ((score, position) for score, position in candidates if score >= qualified_threshold),
        key=lambda item: item[1],
    )
    if not qualified:
        return None
    clusters: list[list[tuple[int, int]]] = []
    for item in qualified:
        if not clusters or item[1] - clusters[-1][-1][1] > 4:
            clusters.append([item])
        else:
            clusters[-1].append(item)
    selected_cluster = clusters[-1] if prefer_high else clusters[0]
    return max(selected_cluster)[1]


def _global_frame_detect(source_image: Image.Image) -> dict[str, int] | None:
    # 1. Resize to a small size for fast pure-Python analysis
    target_size = 100
    w, h = source_image.size
    scale_x = w / target_size
    scale_y = h / target_size
    
    small = source_image.convert("L").resize((target_size, target_size), Image.Resampling.BILINEAR)
    filtered = small.filter(ImageFilter.MedianFilter(size=3))
    edges = ImageOps.autocontrast(filtered.filter(ImageFilter.FIND_EDGES))
    pixels = edges.load()
    
    # 2. Compute 1D projections (sums along rows and columns)
    col_sums = [sum(pixels[x, y] for y in range(target_size)) for x in range(target_size)]
    row_sums = [sum(pixels[x, y] for x in range(target_size)) for y in range(target_size)]
    
    # 3. Find candidates on left/right and top/bottom halves
    left_candidates = sorted(range(5, target_size // 2 - 5), key=lambda x: col_sums[x], reverse=True)[:5]
    right_candidates = sorted(range(target_size // 2 + 5, target_size - 5), key=lambda x: col_sums[x], reverse=True)[:5]
    top_candidates = sorted(range(5, target_size // 2 - 5), key=lambda y: row_sums[y], reverse=True)[:5]
    bottom_candidates = sorted(range(target_size // 2 + 5, target_size - 5), key=lambda y: row_sums[y], reverse=True)[:5]
    
    best_score = -1
    best_box = None
    
    # 4. Evaluate all candidate combinations
    for left in left_candidates:
        for right in right_candidates:
            for top in top_candidates:
                for bottom in bottom_candidates:
                    box_w = right - left
                    box_h = bottom - top
                    if box_w < 20 or box_h < 20: # Too small
                        continue
                    
                    l_score = sum(pixels[left, y] for y in range(top, bottom + 1))
                    r_score = sum(pixels[right, y] for y in range(top, bottom + 1))
                    t_score = sum(pixels[x, top] for x in range(left, right + 1))
                    b_score = sum(pixels[x, bottom] for x in range(left, right + 1))
                    
                    # Normalize by border length to avoid bias towards large boxes
                    total_score = (l_score + r_score) / box_h + (t_score + b_score) / box_w

                    # Uniformity bonus: prefer boxes whose interior has low edge-energy
                    # (a flat / solid-colour placeholder has near-zero edge values inside,
                    # whereas a textured region or already-filled artwork has high edge energy).
                    # Bonus is at most +20 %, avoiding any reversal of clearly stronger frames.
                    sample_xs = range(
                        left + max(2, box_w // 8),
                        right - max(2, box_w // 8),
                        max(1, box_w // 6),
                    )
                    sample_ys = range(
                        top + max(2, box_h // 8),
                        bottom - max(2, box_h // 8),
                        max(1, box_h // 6),
                    )
                    inner_edge_vals = [pixels[x, y] for x in sample_xs for y in sample_ys]
                    if inner_edge_vals:
                        mean_inner_edge = sum(inner_edge_vals) / len(inner_edge_vals)
                        # mean_inner_edge ≈ 0  → very uniform interior → full bonus
                        # mean_inner_edge ≥ 40 → noisy interior         → no bonus
                        uniformity_bonus = max(0.0, (40.0 - mean_inner_edge) / 40.0) * 0.20
                        total_score *= 1.0 + uniformity_bonus

                    if total_score > best_score:
                        best_score = total_score
                        best_box = (left, top, box_w, box_h)
                        
    if best_box and best_score > 60:  # Coarse threshold
        left, top, bw, bh = best_box
        return {
            "x": round(left * scale_x),
            "y": round(top * scale_y),
            "width": round(bw * scale_x),
            "height": round(bh * scale_y),
        }
    return None


def _detect_uniform_region_pil(
    source_image: Image.Image,
    min_area_ratio: float = 0.04,
    max_area_ratio: float = 0.90,
) -> dict[str, int] | None:
    """
    Detect the most uniformly-coloured rectangular region in the image.

    Designed for device mockups (phone / tablet / laptop screens) where the
    placeholder is a flat white, light-grey or dark rectangle surrounded by a
    differently-tinted bezel.  The function **only returns a result** when the
    found region is at least 2.5× more uniform than the image average —
    preventing false positives on single-colour or already-filled mockups.

    Uses PIL only — no OpenCV dependency.
    """
    target_size = 80
    w, h = source_image.size
    scale_x = w / target_size
    scale_y = h / target_size

    small = source_image.convert("L").resize(
        (target_size, target_size), Image.Resampling.BILINEAR
    )
    pixels = small.load()

    # Measure the global image standard deviation at reduced resolution.
    all_vals = [
        pixels[x, y]
        for x in range(0, target_size, 2)
        for y in range(0, target_size, 2)
    ]
    global_mean = sum(all_vals) / len(all_vals)
    global_std = (
        sum((v - global_mean) ** 2 for v in all_vals) / len(all_vals)
    ) ** 0.5

    # If the image itself is nearly uniform there is no distinct region to isolate.
    if global_std < 8:
        return None

    best_box: tuple[int, int, int, int] | None = None
    best_std = float("inf")
    step = 4
    min_pix = min_area_ratio * target_size * target_size
    max_pix = max_area_ratio * target_size * target_size

    for x1 in range(step, target_size // 2 - step, step):
        for y1 in range(step, target_size // 2 - step, step):
            for x2 in range(target_size // 2, target_size - step, step):
                for y2 in range(target_size // 2, target_size - step, step):
                    box_w = x2 - x1
                    box_h = y2 - y1
                    if not (min_pix <= box_w * box_h <= max_pix):
                        continue
                    if box_w < 8 or box_h < 8:
                        continue

                    # Sample interior pixels inset by ~15 % to avoid border bleed.
                    inset_x = max(1, box_w // 6)
                    inset_y = max(1, box_h // 6)
                    sx = max(1, box_w // 8)
                    sy = max(1, box_h // 8)

                    interior = [
                        pixels[x, y]
                        for x in range(x1 + inset_x, x2 - inset_x, sx)
                        for y in range(y1 + inset_y, y2 - inset_y, sy)
                    ]
                    if len(interior) < 6:
                        continue

                    mean_v = sum(interior) / len(interior)
                    std = (
                        sum((v - mean_v) ** 2 for v in interior) / len(interior)
                    ) ** 0.5

                    if std < best_std:
                        best_std = std
                        best_box = (x1, y1, box_w, box_h)

    # Only return if the found region is clearly more uniform than the overall image.
    # A ratio of 2.5 means the interior is ≥2.5× flatter than the mean — strong signal.
    if best_box is None or best_std * 2.5 >= global_std:
        return None

    x1, y1, bw, bh = best_box
    return {
        "x": round(x1 * scale_x),
        "y": round(y1 * scale_y),
        "width": round(bw * scale_x),
        "height": round(bh * scale_y),
    }


def refine_artwork_area(image_path: Path, proposed_area: dict[str, int], blur_size: int = 3) -> dict[str, int]:
    """Snap an outer or approximate frame proposal to its visible inner opening."""
    with Image.open(image_path) as source:
        # Stage 1 — edge-projection global search (handles off-centre frames, posters, etc.)
        coarse = _global_frame_detect(source)
        if coarse:
            proposed_area = coarse
        else:
            # Stage 2 — uniform-region search (device screens: phone / tablet / laptop).
            # Only used when the edge-projection search found nothing convincing.
            uniform = _detect_uniform_region_pil(source)
            if uniform:
                proposed_area = uniform

        # Apply a median filter to suppress fine high-frequency noise and textures (like wood grains)
        # while preserving perfectly sharp structural borders for pixel-accurate snapping.
        filtered = source.convert("L").filter(ImageFilter.MedianFilter(size=blur_size))
    width, height = filtered.size
    x = int(proposed_area["x"])
    y = int(proposed_area["y"])
    area_width = int(proposed_area["width"])
    area_height = int(proposed_area["height"])
    radius = max(18, round(min(area_width, area_height) * 0.16))
    edges = ImageOps.autocontrast(filtered.filter(ImageFilter.FIND_EDGES))
    pixels = edges.load()

    vertical_start = max(0, y - radius // 2)
    vertical_end = min(height, y + area_height + radius // 2)
    horizontal_start = max(0, x - radius // 2)
    horizontal_end = min(width, x + area_width + radius // 2)
    inset = max(3, round(min(area_width, area_height) * 0.025))
    horizontal_depth = max(inset + 10, round(area_width * 0.34))
    vertical_depth = max(inset + 10, round(area_height * 0.34))
    left = _inner_boundary_line(
        pixels,
        vertical=True,
        start=x + inset,
        end=x + horizontal_depth,
        span_start=vertical_start,
        span_end=vertical_end,
        boundary=width,
        prefer_high=True,
    ) or _strongest_line(
        pixels,
        vertical=True,
        center=x,
        radius=radius,
        span_start=vertical_start,
        span_end=vertical_end,
        boundary=width,
    )
    right = _inner_boundary_line(
        pixels,
        vertical=True,
        start=x + area_width - horizontal_depth,
        end=x + area_width - inset,
        span_start=vertical_start,
        span_end=vertical_end,
        boundary=width,
        prefer_high=False,
    ) or _strongest_line(
        pixels,
        vertical=True,
        center=x + area_width,
        radius=radius,
        span_start=vertical_start,
        span_end=vertical_end,
        boundary=width,
    )
    top = _inner_boundary_line(
        pixels,
        vertical=False,
        start=y + inset,
        end=y + vertical_depth,
        span_start=horizontal_start,
        span_end=horizontal_end,
        boundary=height,
        prefer_high=True,
    ) or _strongest_line(
        pixels,
        vertical=False,
        center=y,
        radius=radius,
        span_start=horizontal_start,
        span_end=horizontal_end,
        boundary=height,
    )
    bottom = _inner_boundary_line(
        pixels,
        vertical=False,
        start=y + area_height - vertical_depth,
        end=y + area_height - inset,
        span_start=horizontal_start,
        span_end=horizontal_end,
        boundary=height,
        prefer_high=False,
    ) or _strongest_line(
        pixels,
        vertical=False,
        center=y + area_height,
        radius=radius,
        span_start=horizontal_start,
        span_end=horizontal_end,
        boundary=height,
    )
    if right - left < 20 or bottom - top < 20:
        return proposed_area
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _inner_strongest_line(
    pixels,
    *,
    vertical: bool,
    center: int,
    radius: int,
    span_start: int,
    span_end: int,
    boundary: int,
    prefer_high: bool,
) -> int:
    """Scan local candidates and prefer the inner boundary when multiple edge clusters are present."""
    candidates: list[tuple[int, int]] = []
    start = max(1, center - radius)
    end = min(boundary - 2, center + radius)
    for position in range(start, end + 1):
        if vertical:
            score = sum(pixels[position, offset] for offset in range(span_start, span_end))
        else:
            score = sum(pixels[offset, position] for offset in range(span_start, span_end))
        candidates.append((score, position))
    if not candidates:
        return center
    
    max_score = max(score for score, _ in candidates)
    if max_score < 100:  # Noise threshold
        return center
    
    # Filter qualified coordinates with at least 25% of maximum edge contrast
    # Lowered from 0.45 to 0.25 to reliably detect the inner opening border
    # even when there is an extremely strong outer frame border nearby.
    threshold = max(200, round(max_score * 0.25))
    qualified = [pos for score, pos in candidates if score >= threshold]
    if not qualified:
        return center
    
    # Group coordinates into separate physical edge clusters
    clusters: list[list[int]] = []
    for pos in sorted(qualified):
        if not clusters or pos - clusters[-1][-1] > 3:
            clusters.append([pos])
        else:
            clusters[-1].append(pos)
            
    # Find the peak coordinate inside each cluster
    best_positions = []
    for cluster in clusters:
        best_pos = cluster[0]
        best_val = -1
        for pos in cluster:
            val = next(score for score, p in candidates if p == pos)
            if val > best_val:
                best_val = val
                best_pos = pos
        best_positions.append(best_pos)
        
    if not best_positions:
        return center
        
    # If multiple separate boundary edges exist, prefer the inner opening edge
    if len(best_positions) > 1:
        return best_positions[-1] if prefer_high else best_positions[0]
    return best_positions[0]


def refine_perspective_corners(
    image_path: Path,
    corners: list[dict[str, int]],
    search_radius: int = 60,
    blur_size: int = 3
) -> list[dict[str, int]]:
    """Refine each of the 4 perspective corners locally using classic edge detection."""
    try:
        with Image.open(image_path) as source:
            # Use GaussianBlur(0.5) instead of MedianFilter to preserve perfectly sharp
            # and very thin inner boundary lines, preventing them from being erased.
            filtered = source.convert("L").filter(ImageFilter.GaussianBlur(radius=0.5))
            
        width, height = filtered.size
        edges = ImageOps.autocontrast(filtered.filter(ImageFilter.FIND_EDGES))
        pixels = edges.load()
        
        refined_corners = []
        for idx, p in enumerate(corners):
            cx = int(p["x"])
            cy = int(p["y"])
            
            # Local span range for edge alignment
            span_start_v = max(0, cy - search_radius)
            span_end_v = min(height, cy + search_radius)
            
            span_start_h = max(0, cx - search_radius)
            span_end_h = min(width, cx + search_radius)
            
            # Map corner index to directional inner edge preferences:
            # Corner 0 (Top-Left): inner opening is at larger x, larger y (True, True)
            # Corner 1 (Top-Right): inner opening is at smaller x, larger y (False, True)
            # Corner 2 (Bottom-Right): inner opening is at smaller x, smaller y (False, False)
            # Corner 3 (Bottom-Left): inner opening is at larger x, smaller y (True, False)
            prefer_high_x = idx in (0, 3)
            prefer_high_y = idx in (0, 1)
            
            # 1. Snap vertical boundary (X coordinate)
            snapped_x = _inner_strongest_line(
                pixels,
                vertical=True,
                center=cx,
                radius=search_radius,
                span_start=span_start_v,
                span_end=span_end_v,
                boundary=width,
                prefer_high=prefer_high_x
            )
            
            # 2. Snap horizontal boundary (Y coordinate)
            snapped_y = _inner_strongest_line(
                pixels,
                vertical=False,
                center=cy,
                radius=search_radius,
                span_start=span_start_h,
                span_end=span_end_h,
                boundary=height,
                prefer_high=prefer_high_y
            )
            
            # Safety gate: if snapping shifts the coordinate by more than or equal to search_radius, reject
            if abs(snapped_x - cx) >= search_radius:
                snapped_x = cx
            if abs(snapped_y - cy) >= search_radius:
                snapped_y = cy
                
            refined_corners.append({
                "x": snapped_x,
                "y": snapped_y
            })
            
        return refined_corners
    except Exception:
        # Fallback to unrefined corners if anything fails
        return corners
