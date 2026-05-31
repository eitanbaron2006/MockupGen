import numpy as np
from PIL import Image
import math

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

src_pts = [{"x": 0.0, "y": 0.0}, {"x": 99.0, "y": 0.0}, {"x": 99.0, "y": 99.0}, {"x": 0.0, "y": 99.0}]
dst_pts = [{"x": 10.0, "y": 15.0}, {"x": 85.0, "y": 5.0}, {"x": 95.0, "y": 90.0}, {"x": 5.0, "y": 80.0}]

h = _homography(src_pts, dst_pts)
inv = np.linalg.inv(h.reshape(3, 3)).reshape(9)
inv_norm = inv / inv[8]

print("Original inv[8]:", inv[8])
print("\nMapping corners from destination to source:")
for i, pt in enumerate(dst_pts):
    expected = src_pts[i]
    x_d, y_d = pt["x"], pt["y"]
    
    # 1. Manual map
    man_x, man_y = _apply_h(inv, x_d, y_d)
    
    # 2. PIL Un-normalized math (assumes 9th coefficient is 1.0)
    unnorm_den = inv[6]*x_d + inv[7]*y_d + 1.0
    unnorm_x = (inv[0]*x_d + inv[1]*y_d + inv[2]) / unnorm_den
    unnorm_y = (inv[3]*x_d + inv[4]*y_d + inv[5]) / unnorm_den
    
    # 3. PIL Normalized math (assumes 9th coefficient is 1.0 of normalized matrix)
    norm_den = inv_norm[6]*x_d + inv_norm[7]*y_d + 1.0
    norm_x = (inv_norm[0]*x_d + inv_norm[1]*y_d + inv_norm[2]) / norm_den
    norm_y = (inv_norm[3]*x_d + inv_norm[4]*y_d + inv_norm[5]) / norm_den
    
    print(f"Corner {i} (dst: {x_d}, {y_d}) -> expected: ({expected['x']}, {expected['y']})")
    print(f"  Manual:            ({man_x:.4f}, {man_y:.4f})")
    print(f"  PIL Un-normalized: ({unnorm_x:.4f}, {unnorm_y:.4f})")
    print(f"  PIL Normalized:   ({norm_x:.4f}, {norm_y:.4f})")
