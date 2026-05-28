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
                    
                    if total_score > best_score:
                        best_score = total_score
                        best_box = (left, top, box_w, box_h)
                        
    if best_box and best_score > 60: # Coarse threshold
        left, top, box_w, box_h = best_box
        return {
            "x": round(left * scale_x),
            "y": round(top * scale_y),
            "width": round(box_w * scale_x),
            "height": round(box_h * scale_y)
        }
    return None


def refine_artwork_area(image_path: Path, proposed_area: dict[str, int]) -> dict[str, int]:
    """Snap an outer or approximate frame proposal to its visible inner opening."""
    with Image.open(image_path) as source:
        # Try global coarse frame detection first to handle off-center frames beautifully!
        coarse = _global_frame_detect(source)
        if coarse:
            proposed_area = coarse
            
        # Apply a median filter to suppress fine high-frequency noise and textures (like wood grains)
        # while preserving perfectly sharp structural borders for pixel-accurate snapping.
        filtered = source.convert("L").filter(ImageFilter.MedianFilter(size=3))
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
