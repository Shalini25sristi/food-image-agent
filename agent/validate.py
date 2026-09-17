"""Automatic image quality validation using OpenCV.

Checks performed:
  - decodability / corruption
  - minimum resolution
  - blur (Laplacian variance)
  - exposure (too dark / washed out)
  - extreme aspect ratios
  - framing: subject roughly centred, not excessively zoomed in or heavily
    cropped at the frame edges (saliency-map analysis)
"""

import logging
from dataclasses import dataclass, field

import numpy as np

try:  # OpenCV is optional - it enables saliency-based framing analysis.
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

from .config import Config

log = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    ok: bool
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    width: int = 0
    height: int = 0


def _saliency_map(img: np.ndarray) -> np.ndarray | None:
    """Return a float saliency map in [0, 1], or None if unavailable."""
    if cv2 is None:
        return None
    try:
        sal = cv2.saliency.StaticSaliencySpectralResidual_create()
        ok, smap = sal.computeSaliency(img)
        if not ok:
            return None
        m = smap.astype(np.float32)
        mx = float(m.max())
        return m / mx if mx > 0 else None
    except Exception as e:
        log.debug("Saliency unavailable: %s", e)
        return None


def _framing_metrics(img: np.ndarray) -> tuple[float, float, float] | None:
    """Return (center_offset, border_cut, coverage) in [0,1] using saliency.

    center_offset: distance of subject centroid from image centre
                   (0 = perfectly centred, 1 = at a corner).
    border_cut:    fraction of salient pixels touching the outer 3% frame
                   border (high => subject is cropped by the frame).
    coverage:      fraction of the frame occupied by the subject
                   (very high => excessively zoomed in).
    """
    # Saliency is scale-invariant but expensive on full-resolution photos, so
    # analyse a downscaled copy (big speed-up on 12-24 MP images).
    if cv2 is not None:
        h, w = img.shape[:2]
        scale = 800.0 / max(h, w)
        if scale < 1.0:
            img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))))
    smap = _saliency_map(img)
    if smap is None:
        return None
    h, w = smap.shape
    thresh = max(0.25, float(smap.mean()) + float(smap.std()))
    mask = (smap >= thresh).astype(np.uint8)
    total = int(mask.sum())
    if total < 0.01 * w * h:
        return None

    ys, xs = np.nonzero(mask)
    cx, cy = xs.mean() / w, ys.mean() / h
    center_offset = min(1.0, ((cx - 0.5) ** 2 + (cy - 0.5) ** 2) ** 0.5 / 0.7071 * 2)

    b = max(2, int(0.03 * min(h, w)))
    border = np.zeros_like(mask)
    border[:b, :] = border[-b:, :] = 1
    border[:, :b] = border[:, -b:] = 1
    border_cut = float((mask & border).sum()) / total

    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    coverage = ((x1 - x0 + 1) * (y1 - y0 + 1)) / (w * h)
    return center_offset, border_cut, coverage


def _load_image(path: str) -> np.ndarray | None:
    """Load as an HxWx3 array (BGR when OpenCV is present, RGB otherwise)."""
    if cv2 is not None:
        return cv2.imread(path)
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            return np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
    except Exception:
        return None


def _laplacian_var(gray: np.ndarray) -> float:
    if cv2 is not None:
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    g = gray.astype(np.float64)
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1]
           + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def validate_image(path: str, cfg: Config) -> ValidationResult:
    reasons: list[str] = []
    score = 0.0

    img = _load_image(path)
    if img is None:
        return ValidationResult(ok=False, reasons=["corrupt or unsupported image file"])

    h, w = img.shape[:2]
    result = ValidationResult(ok=True, width=w, height=h)

    # --- resolution -------------------------------------------------------
    if w < cfg.min_width or h < cfg.min_height:
        reasons.append(f"low resolution ({w}x{h} < {cfg.min_width}x{cfg.min_height})")
    else:
        score += min(1.0, (w * h) / (cfg.target_width * cfg.target_height)) * 40

    # --- aspect ratio ------------------------------------------------------
    aspect = w / h
    if aspect < 0.6 or aspect > 2.2:
        reasons.append(f"extreme aspect ratio ({aspect:.2f})")

    # --- blur --------------------------------------------------------------
    gray = (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if cv2 is not None
            else np.asarray(img).mean(axis=2))
    lap_var = _laplacian_var(gray)
    if lap_var < cfg.blur_threshold:
        reasons.append(f"blurry (Laplacian variance {lap_var:.1f} < {cfg.blur_threshold})")
    else:
        score += min(1.0, lap_var / (cfg.blur_threshold * 6)) * 30

    # --- exposure ----------------------------------------------------------
    mean_brightness = float(gray.mean())
    if mean_brightness < 35:
        reasons.append(f"too dark (mean brightness {mean_brightness:.0f})")
    elif mean_brightness > 225:
        reasons.append(f"overexposed (mean brightness {mean_brightness:.0f})")
    else:
        score += 10

    # --- framing / centring / zoom ----------------------------------------
    metrics = _framing_metrics(img)
    if metrics is not None:
        center_offset, border_cut, coverage = metrics
        if center_offset > 0.60:
            reasons.append(f"subject off-centre (offset {center_offset:.2f})")
        else:
            score += (1 - center_offset) * 20
        if border_cut > 0.55:
            reasons.append(f"subject heavily cropped at edges ({border_cut:.0%})")
        # A dish filling the frame is normal in food photography; only flag
        # it as "excessively zoomed" when the subject ALSO spills off the
        # frame edges on all sides (i.e. we are inside the food, not at it).
        if coverage > 0.95 and border_cut > 0.55:
            reasons.append(f"excessively zoomed in (subject fills {coverage:.0%} of frame)")

    result.ok = not reasons
    result.reasons = reasons
    result.score = round(score, 2)
    return result
