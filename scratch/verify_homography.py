import numpy as np
from PIL import Image
import math

def _dist(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])

def _homography(src, dst):
    rows, vals = [], []
    for s, d in zip(src, dst):
        x, y, u, v = s["x"], s["y"], d["x"], d["y"]
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); vals.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y]); vals.append(v)
    h = np.linalg.solve(np.asarray(rows, dtype=np.float64), np.asarray(vals, dtype=np.float64))
    return np.asarray([h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], 1.0], dtype=np.float64)

def _apply_h(m, x, y):
    den = m[6] * x + m[7] * y + m[8]
    return ((m[0] * x + m[1] * y + m[2]) / den, (m[3] * x + m[4] * y + m[5]) / den)

# Source image 100x100
src_img = Image.new("RGB", (100, 100))
# Let's draw some pixel patterns to identify positions
for y in range(100):
    for x in range(100):
        src_img.putpixel((x, y), (x * 2, y * 2, 128))

src_pts = [{"x": 0.0, "y": 0.0}, {"x": 99.0, "y": 0.0}, {"x": 99.0, "y": 99.0}, {"x": 0.0, "y": 99.0}]
# Distorted quad
dst_pts = [{"x": 10.0, "y": 15.0}, {"x": 85.0, "y": 5.0}, {"x": 95.0, "y": 90.0}, {"x": 5.0, "y": 80.0}]

h = _homography(src_pts, dst_pts)
inv = np.linalg.inv(h.reshape(3, 3)).reshape(9)

# 1. Manual mapping using _apply_h
manual_img = Image.new("RGB", (100, 100), (0, 0, 0))
for y_d in range(100):
    for x_d in range(100):
        # We check if point is inside destination quad (simplifying: just map all and clamp)
        x_s, y_s = _apply_h(inv, x_d, y_d)
        if 0 <= x_s < 100 and 0 <= y_s < 100:
            # Nearest neighbor sampling for simple verification
            val = src_img.getpixel((int(x_s), int(y_s)))
            manual_img.putpixel((x_d, y_d), val)

# 2. PIL transform with un-normalized inv[:8]
pil_unnorm = src_img.transform((100, 100), Image.Transform.PERSPECTIVE, inv[:8], Image.Resampling.NEAREST)

# 3. PIL transform with normalized inv
inv_norm = inv / inv[8]
pil_norm = src_img.transform((100, 100), Image.Transform.PERSPECTIVE, inv_norm[:8], Image.Resampling.NEAREST)

# Compare differences
diff_unnorm = np.sum(np.abs(np.array(manual_img) - np.array(pil_unnorm)))
diff_norm = np.sum(np.abs(np.array(manual_img) - np.array(pil_norm)))

print(f"Difference Manual vs PIL Un-normalized: {diff_unnorm}")
print(f"Difference Manual vs PIL Normalized: {diff_norm}")
