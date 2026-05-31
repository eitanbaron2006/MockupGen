import numpy as np
from PIL import Image, ImageOps
import math

def _homography(src, dst):
    rows, vals = [], []
    for s, d in zip(src, dst):
        x, y, u, v = s["x"], s["y"], d["x"], d["y"]
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); vals.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y]); vals.append(v)
    h = np.linalg.solve(np.asarray(rows, dtype=np.float64), np.asarray(vals, dtype=np.float64))
    return np.asarray([h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], 1.0], dtype=np.float64)

# Create 100x100 source image with 100% alpha (solid red)
src = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
dst_pts = [{"x": 10.0, "y": 15.0}, {"x": 85.0, "y": 5.0}, {"x": 95.0, "y": 90.0}, {"x": 5.0, "y": 80.0}]

# 1. Without padding
src_pts_nopad = [{"x": 0.0, "y": 0.0}, {"x": 99.0, "y": 0.0}, {"x": 99.0, "y": 99.0}, {"x": 0.0, "y": 99.0}]
h_nopad = _homography(src_pts_nopad, dst_pts)
inv_nopad = np.linalg.inv(h_nopad.reshape(3, 3)).reshape(9)
inv_nopad = inv_nopad / inv_nopad[8]

warped_nopad = src.transform((100, 100), Image.Transform.PERSPECTIVE, inv_nopad[:8], Image.Resampling.BICUBIC)
alpha_nopad = np.array(warped_nopad)[:, :, 3]

# 2. With padding of 4 pixels
pad = 4
padded = Image.new("RGBA", (100 + 2 * pad, 100 + 2 * pad))
padded.paste(src, (pad, pad))

# Repeat edges
left = padded.crop((pad, pad, pad + 1, pad + 100))
padded.paste(left.resize((pad, 100), Image.Resampling.NEAREST), (0, pad))

right = padded.crop((pad + 99, pad, pad + 100, pad + 100))
padded.paste(right.resize((pad, 100), Image.Resampling.NEAREST), (pad + 100, pad))

top = padded.crop((0, pad, 100 + 2 * pad, pad + 1))
padded.paste(top.resize((100 + 2 * pad, pad), Image.Resampling.NEAREST), (0, 0))

bottom = padded.crop((0, pad + 99, 100 + 2 * pad, pad + 100))
padded.paste(bottom.resize((100 + 2 * pad, pad), Image.Resampling.NEAREST), (0, pad + 100))

src_pts_pad = [
    {"x": float(pad), "y": float(pad)},
    {"x": float(pad + 99), "y": float(pad)},
    {"x": float(pad + 99), "y": float(pad + 99)},
    {"x": float(pad), "y": float(pad + 99)}
]
h_pad = _homography(src_pts_pad, dst_pts)
inv_pad = np.linalg.inv(h_pad.reshape(3, 3)).reshape(9)
inv_pad = inv_pad / inv_pad[8]

warped_pad = padded.transform((100, 100), Image.Transform.PERSPECTIVE, inv_pad[:8], Image.Resampling.BICUBIC)
alpha_pad = np.array(warped_pad)[:, :, 3]

# Let's check some pixels near the top-left destination corner (10.0, 15.0)
print("Comparing alpha values at pixels around top-left corner (10, 15):")
for dy in range(-2, 3):
    for dx in range(-2, 3):
        x, y = 10 + dx, 15 + dy
        a_no = alpha_nopad[y, x]
        a_pad = alpha_pad[y, x]
        # We only care about pixels that are supposed to be inside or on the edge
        # If both are 0, it's outside. If there's a difference, print it!
        if a_no != a_pad:
            print(f"Pixel ({x}, {y}): No-Pad Alpha = {a_no}, Pad Alpha = {a_pad} (Diff: {int(a_pad) - int(a_no)})")
