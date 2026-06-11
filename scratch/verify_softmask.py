"""Verify vectorized _precision_soft_mask matches the old per-row loop and time it."""
import time
import numpy as np

from services.green_frame_mockup_service import (
    GreenFrameSettings,
    _precision_soft_mask,
    _sample_grid,
    _blur_float_field,
    _clamp,
)


def old_precision_soft_mask(region_mask, alpha_mask, settings):
    q = int(_clamp(settings.mask_build_quality * 2, 2, 8))
    h, w = region_mask.shape
    hi_h = h * q
    hi_w = w * q
    hi = np.zeros((hi_h, hi_w), dtype=np.float32)
    xs = (np.arange(hi_w, dtype=np.float32) + 0.5) / q - 0.5
    region_float = region_mask.astype(np.float32)
    for hy in range(hi_h):
        sy = (hy + 0.5) / q - 0.5
        ys = np.full(hi_w, sy, dtype=np.float32)
        region_val = _sample_grid(region_float, xs, ys)
        alpha_val = _sample_grid(alpha_mask, xs, ys)
        v = np.clip(alpha_val, 0.0, 1.0)
        v[(region_val > 0.98) & (v > 0.72)] = 1.0
        v[region_val <= 0.001] = 0.0
        hi[hy] = v
    hi_radius = max(1, round(settings.feather_radius * q * 0.45)) if settings.feather_radius > 0 else 0
    if hi_radius > 0:
        hi = _blur_float_field(hi, hi_radius)
    out = hi.reshape(h, q, w, q).mean(axis=(1, 3)).astype(np.float32)
    out[out < 0.01] = 0.0
    return np.clip(out, 0.0, 1.0)


rng = np.random.default_rng(42)
h, w = 420, 600
region = np.zeros((h, w), dtype=bool)
region[40:380, 60:540] = True
alpha = np.clip(rng.random((h, w)).astype(np.float32) * region + rng.random((h, w)).astype(np.float32) * 0.05, 0, 1)
settings = GreenFrameSettings(mask_build_quality=2, feather_radius=2)

t0 = time.perf_counter()
old = old_precision_soft_mask(region, alpha, settings)
t1 = time.perf_counter()
new = _precision_soft_mask(region, alpha, settings)
t2 = time.perf_counter()

diff = np.abs(old - new).max()
print(f"max abs diff: {diff:.2e}")
print(f"old: {t1 - t0:.3f}s   new: {t2 - t1:.3f}s   speedup: {(t1 - t0) / (t2 - t1):.1f}x")
assert diff < 1e-5, "vectorized soft mask diverges from the reference loop"
print("OK")
