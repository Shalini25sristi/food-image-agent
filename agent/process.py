"""Image processing: smart crop, resize to 1800x1200, compress below 10 MB."""

import logging
import os

import numpy as np
from PIL import Image, ImageOps

try:  # optional - enables saliency-aware cropping
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

from .config import Config

log = logging.getLogger(__name__)


def _salient_centroid(path: str) -> tuple[float, float] | None:
    """Relative (x, y) of the most salient region, or None."""
    if cv2 is None:
        return None
    try:
        img = cv2.imread(path)
        if img is None:
            return None
        h, w = img.shape[:2]
        scale = 900.0 / max(h, w)
        if scale < 1.0:
            img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))))
        sal = cv2.saliency.StaticSaliencySpectralResidual_create()
        ok, smap = sal.computeSaliency(img)
        if not ok:
            return None
        m = smap.astype(np.float32)
        if m.max() <= 0:
            return None
        m /= m.max()
        mask = (m >= max(0.25, m.mean() + m.std())).astype(np.uint8)
        if mask.sum() < 0.01 * mask.size:
            return None
        ys, xs = np.nonzero(mask)
        h, w = mask.shape
        return float(xs.mean() / w), float(ys.mean() / h)
    except Exception:
        return None


def _smart_crop(img: Image.Image, target_ratio: float, path: str) -> Image.Image:
    """Crop to the target aspect ratio, centred on the salient subject."""
    w, h = img.size
    ratio = w / h
    if abs(ratio - target_ratio) < 0.01:
        return img

    centroid = _salient_centroid(path) or (0.5, 0.5)
    cx, cy = centroid

    if ratio > target_ratio:  # too wide -> crop width
        new_w = int(h * target_ratio)
        left = int(np.clip(cx * w - new_w / 2, 0, w - new_w))
        return img.crop((left, 0, left + new_w, h))
    else:  # too tall -> crop height
        new_h = int(w / target_ratio)
        top = int(np.clip(cy * h - new_h / 2, 0, h - new_h))
        return img.crop((0, top, w, top + new_h))


def process_image(input_path: str, output_path: str, cfg: Config) -> tuple[int, int]:
    """Produce the final menu image. Returns (width*height pixels, file bytes)."""
    img = Image.open(input_path)
    img = ImageOps.exif_transpose(img).convert("RGB")

    target_ratio = cfg.target_width / cfg.target_height
    img = _smart_crop(img, target_ratio, input_path)
    img = img.resize((cfg.target_width, cfg.target_height), Image.LANCZOS)

    max_bytes = int(cfg.max_file_size_mb * 1024 * 1024)
    quality = 92
    while True:
        img.save(output_path, "JPEG", quality=quality, optimize=True, progressive=True)
        size = os.path.getsize(output_path)
        if size <= max_bytes or quality <= 60:
            break
        quality -= 8
    log.info("Processed image saved: %s (%d bytes, q=%d)", output_path, size, quality)
    return cfg.target_width * cfg.target_height, size
