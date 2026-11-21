"""Full offline Demo Mode for Vertigo.

When enabled (via the sidebar toggle or ``VERTIGO_DEMO=1``), every generation
workflow — image T2I/edit, vector art/trace, remove background, upscale, TTS
and transcription — runs through the real background job queue with staged
loading, live logs, cancellation support and locally synthesized sample media,
without spending any fal.ai credits or requiring an API key.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import random
import struct
import time
import wave
from typing import TYPE_CHECKING, Any, Callable

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from . import config

if TYPE_CHECKING:
    from .jobs import Job
    from .store import Library

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

#: Global timing multiplier (tests can set this to 0.0 for instant runs).
DELAY_SCALE = 1.0


# --------------------------------------------------------------------------- #
# Mode state
# --------------------------------------------------------------------------- #


def env_forced() -> bool:
    """True when ``VERTIGO_DEMO`` explicitly controls demo mode."""
    raw = os.environ.get("VERTIGO_DEMO")
    return raw is not None and raw.strip() != ""


def is_enabled() -> bool:
    """Return True when Demo Mode is active."""
    raw = os.environ.get("VERTIGO_DEMO")
    if raw is not None and raw.strip() != "":
        lowered = raw.strip().lower()
        if lowered in _TRUTHY:
            return True
        if lowered in _FALSY:
            return False
    return bool(config.load_config().get("demo_mode", False))


def set_enabled(enabled: bool) -> None:
    """Persist the user's Demo Mode preference."""
    config.update_config(demo_mode=bool(enabled))


# --------------------------------------------------------------------------- #
# Job simulation
# --------------------------------------------------------------------------- #


def _sleep_cancelable(seconds: float, is_cancelled: Callable[[], bool]) -> bool:
    """Sleep in small slices so cancellation takes effect promptly."""
    remaining = max(0.0, seconds * DELAY_SCALE)
    while remaining > 0:
        if is_cancelled():
            return False
        step = min(0.1, remaining)
        time.sleep(step)
        remaining -= step
    return not is_cancelled()


def run_job(
    job: Job,
    library: Library,
    *,
    on_enqueue: Callable[[str], None] | None = None,
    on_log: Callable[[list[str]], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any] | None:
    """Execute a simulated job with realistic queue/progress timings and logs.

    Returns ``None`` if the job was cancelled mid-flight.
    """
    cancelled = is_cancelled or (lambda: False)
    log = on_log or (lambda _msgs: None)
    model_name = config.endpoint_label(job.endpoint)

    if on_enqueue:
        on_enqueue(f"demo-{job.id}")

    stages = [
        (0.55, f"[demo] Queued {model_name} ({job.endpoint})"),
        (0.95, "[demo] Preparing inputs and generation parameters…"),
        (1.05, "[demo] Synthesizing sample preview output…"),
        (0.35, "[demo] Finalizing asset (0 credits used)."),
    ]
    for duration, message in stages:
        if cancelled():
            return None
        log([message])
        if not _sleep_cancelable(duration, cancelled):
            return None

    return build_sample_result(job, library)


# --------------------------------------------------------------------------- #
# Sample generators
# --------------------------------------------------------------------------- #


_PALETTES = [
    ((22, 34, 68), (88, 72, 214), (244, 143, 177), (255, 224, 178)),
    ((13, 59, 64), (32, 148, 139), (129, 230, 217), (254, 240, 138)),
    ((58, 22, 54), (178, 58, 114), (249, 132, 74), (255, 227, 179)),
    ((18, 28, 48), (45, 108, 223), (112, 214, 255), (235, 246, 255)),
    ((42, 30, 22), (186, 98, 42), (242, 184, 102), (255, 243, 214)),
    ((24, 46, 36), (64, 145, 108), (149, 213, 178), (216, 243, 220)),
]


def _seed_rng(*parts: object) -> random.Random:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _resolve_dimensions(arguments: dict[str, Any]) -> tuple[int, int]:
    """Choose sensible preview dimensions matching the requested size/aspect."""
    size = arguments.get("image_size")
    if isinstance(size, dict) and size.get("width") and size.get("height"):
        w = max(256, min(1024, int(size["width"])))
        h = max(256, min(1024, int(size["height"])))
        return w, h

    size_str = str(size or "").lower()
    size_table = {
        "square_hd": (768, 768),
        "square": (768, 768),
        "landscape_4_3": (832, 624),
        "landscape_16_9": (896, 504),
        "portrait_4_3": (624, 832),
        "portrait_16_9": (504, 896),
    }
    if size_str in size_table:
        return size_table[size_str]

    ar = str(arguments.get("aspect_ratio") or "").strip().lower()
    if ":" in ar:
        try:
            left, right = ar.split(":", 1)
            rw, rh = float(left), float(right)
            if rw > 0 and rh > 0:
                base = 768
                if rw >= rh:
                    w = min(960, int(base * min(rw / rh, 2.4)))
                    h = max(256, int(round(w * (rh / rw))))
                else:
                    h = min(960, int(base * min(rh / rw, 2.4)))
                    w = max(256, int(round(h * (rw / rh))))
                return w, h
        except ValueError:
            pass

    return 768, 768


def _encode_image(image: Image.Image, output_format: str) -> tuple[bytes, str, str]:
    fmt = (output_format or "png").strip().lower()
    buf = io.BytesIO()
    if fmt in ("jpg", "jpeg"):
        image.convert("RGB").save(buf, format="JPEG", quality=90)
        return buf.getvalue(), "image/jpeg", ".jpg"
    if fmt == "webp":
        image.save(buf, format="WEBP", quality=88)
        return buf.getvalue(), "image/webp", ".webp"
    image.save(buf, format="PNG")
    return buf.getvalue(), "image/png", ".png"


def _render_mesh_artwork(
    width: int,
    height: int,
    *,
    rng: random.Random,
    transparent: bool = False,
) -> Image.Image:
    """Create a smooth, gallery-grade abstract gradient composition using Pillow."""
    palette = rng.choice(_PALETTES)

    if transparent:
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    else:
        # Build a smooth 2D gradient at low resolution then bicubic-upscale.
        gw, gh = 64, 64
        grad = Image.new("RGBA", (gw, gh))
        c0, c1, c2, _ = palette
        pixels = []
        for y in range(gh):
            ty = y / max(gh - 1, 1)
            for x in range(gw):
                tx = x / max(gw - 1, 1)
                w1 = (tx + ty) * 0.5
                w2 = tx * (1.0 - ty)
                r = int(c0[0] * (1 - w1) + c1[0] * w1 * (1 - w2 * 0.5) + c2[0] * w2 * 0.5)
                g = int(c0[1] * (1 - w1) + c1[1] * w1 * (1 - w2 * 0.5) + c2[1] * w2 * 0.5)
                b = int(c0[2] * (1 - w1) + c1[2] * w1 * (1 - w2 * 0.5) + c2[2] * w2 * 0.5)
                pixels.append((max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)), 255))
        grad.putdata(pixels)
        canvas = grad.resize((width, height), Image.Resampling.BICUBIC)

    # Soft luminous orbs blurred onto an overlay layer.
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    short = min(width, height)
    for _ in range(4 if transparent else 5):
        color = rng.choice(palette[1:])
        cx = rng.uniform(0.22, 0.78) * width
        cy = rng.uniform(0.22, 0.78) * height
        rad = rng.uniform(0.16, 0.36) * short
        alpha = rng.randint(155, 225) if transparent else rng.randint(110, 190)
        glow_draw.ellipse(
            (cx - rad, cy - rad, cx + rad, cy + rad),
            fill=(*color, alpha),
        )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(12, short // 18)))
    canvas = Image.alpha_composite(canvas, glow)

    # Crisp geometric focal elements on top.
    shapes = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shapes)
    cx, cy = width * rng.uniform(0.42, 0.58), height * rng.uniform(0.42, 0.58)
    main_r = short * rng.uniform(0.22, 0.30)
    accent = palette[2]
    highlight = palette[3]

    # Translucent glass disc + crisp orbital ring.
    draw.ellipse(
        (cx - main_r, cy - main_r, cx + main_r, cy + main_r),
        fill=(*accent, 210 if transparent else 155),
        outline=(*highlight, 220),
        width=max(2, short // 160),
    )
    inner_r = main_r * 0.58
    dx, dy = rng.uniform(-0.22, 0.22) * main_r, rng.uniform(-0.22, 0.22) * main_r
    draw.ellipse(
        (cx + dx - inner_r, cy + dy - inner_r, cx + dx + inner_r, cy + dy + inner_r),
        fill=(*highlight, 175 if transparent else 135),
    )
    ring_r = main_r * 1.32
    draw.arc(
        (cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r),
        start=rng.randint(0, 180),
        end=rng.randint(220, 340),
        fill=(*highlight, 195),
        width=max(2, short // 180),
    )
    return Image.alpha_composite(canvas, shapes)


def _load_parent_image(job: Job, library: Library) -> Image.Image | None:
    if not job.parent_id:
        return None
    asset = library.get(job.parent_id)
    if asset is None or asset.is_svg:
        return None
    path = library.file_path(asset)
    if not path or not path.exists():
        return None
    try:
        with Image.open(path) as img:
            return img.convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def _render_demo_edit(
    source: Image.Image,
    *,
    rng: random.Random,
    transparent: bool = False,
) -> Image.Image:
    """Create a visibly transformed version of a reference image for demo edits."""
    img = source.copy()
    # Cap size so demo edits stay fast and clean.
    img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    rgb = img.convert("RGB")
    alpha = img.getchannel("A")

    rgb = ImageEnhance.Color(rgb).enhance(rng.uniform(1.12, 1.32))
    rgb = ImageEnhance.Contrast(rgb).enhance(rng.uniform(1.06, 1.18))
    edited = rgb.convert("RGBA")
    edited.putalpha(alpha)

    # Subtle studio light wash across one corner.
    w, h = edited.size
    palette = rng.choice(_PALETTES)
    wash = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(wash)
    cx = w * rng.choice([0.15, 0.85])
    cy = h * rng.choice([0.15, 0.85])
    rad = max(w, h) * 0.65
    draw.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=(*palette[2], 65))
    wash = wash.filter(ImageFilter.GaussianBlur(radius=max(16, min(w, h) // 10)))
    if transparent:
        wash.putalpha(Image.composite(wash.getchannel("A"), Image.new("L", (w, h), 0), alpha))
    return Image.alpha_composite(edited, wash)


def _sample_images(job: Job, library: Library) -> dict[str, Any]:
    args = job.arguments
    num_images = max(1, min(4, int(args.get("num_images") or 1)))
    prompt = str(args.get("prompt") or "demo")
    transparent = bool(
        args.get("transparent_background") or args.get("background") == "transparent"
    )
    output_format = str(args.get("output_format") or "png")
    if transparent and output_format in ("jpg", "jpeg"):
        output_format = "png"

    parent = _load_parent_image(job, library)
    width, height = _resolve_dimensions(args)

    items = []
    for index in range(num_images):
        rng = _seed_rng(job.endpoint, prompt, index, job.id)
        if parent is not None:
            image = _render_demo_edit(parent, rng=rng, transparent=transparent)
        else:
            image = _render_mesh_artwork(width, height, rng=rng, transparent=transparent)
        data, content_type, ext = _encode_image(image, output_format)
        items.append(
            {
                "data": data,
                "content_type": content_type,
                "file_name": f"demo-{index + 1}{ext}",
                "width": image.width,
                "height": image.height,
            }
        )
    return {"images": items}


def _sample_svg(job: Job) -> dict[str, Any]:
    args = job.arguments
    width, height = _resolve_dimensions(args)
    rng = _seed_rng(job.endpoint, args.get("prompt") or job.label, job.id)
    palette = rng.choice(_PALETTES)

    def _hex(rgb: tuple[int, int, int]) -> str:
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

    c0, c1, c2, c3 = (_hex(c) for c in palette)
    cx, cy = width // 2, height // 2
    r1 = int(min(width, height) * 0.28)
    r2 = int(r1 * 0.62)
    wave_y = int(height * 0.72)

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{c0}"/>
      <stop offset="100%" stop-color="{c1}"/>
    </linearGradient>
    <linearGradient id="orb" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{c3}"/>
      <stop offset="100%" stop-color="{c2}"/>
    </linearGradient>
  </defs>
  <rect width="{width}" height="{height}" rx="28" fill="url(#bg)"/>
  <path d="M 0 {wave_y} Q {width // 4} {wave_y - 90}, {width // 2} {wave_y} T {width} {wave_y - 20} L {width} {height} L 0 {height} Z" fill="{c2}" fill-opacity="0.32"/>
  <circle cx="{cx}" cy="{cy - 20}" r="{r1}" fill="url(#orb)"/>
  <circle cx="{cx + r2 // 2}" cy="{cy - r2 // 2}" r="{r2}" fill="{c3}" fill-opacity="0.45"/>
  <circle cx="{cx}" cy="{cy - 20}" r="{int(r1 * 1.28)}" fill="none" stroke="{c3}" stroke-width="4" stroke-dasharray="18 14" stroke-opacity="0.7"/>
</svg>
"""
    item = {
        "data": svg.encode("utf-8"),
        "content_type": "image/svg+xml",
        "file_name": "demo-vector.svg",
        "width": width,
        "height": height,
    }
    if job.save_kind == "image":
        return {"image": item}
    return {"images": [item]}


def _sample_remove_bg(job: Job, library: Library) -> dict[str, Any]:
    source = _load_parent_image(job, library)
    if source is None:
        rng = _seed_rng(job.id, "remove_bg")
        cutout = _render_mesh_artwork(768, 768, rng=rng, transparent=True)
    else:
        cutout = source.copy()
        w, h = cutout.size
        mask = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        pad_x, pad_y = int(w * 0.08), int(h * 0.08)
        draw.ellipse((pad_x, pad_y, w - pad_x, h - pad_y), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(radius=max(6, min(w, h) // 36)))
        existing_alpha = cutout.getchannel("A")
        combined = Image.composite(existing_alpha, Image.new("L", (w, h), 0), mask)
        cutout.putalpha(combined)

    data, content_type, _ = _encode_image(cutout, "png")
    return {
        "image": {
            "data": data,
            "content_type": content_type,
            "file_name": "demo-cutout.png",
            "width": cutout.width,
            "height": cutout.height,
        }
    }


def _sample_upscale(job: Job, library: Library) -> dict[str, Any]:
    factor = float(job.arguments.get("upscale_factor") or 2.0)
    output_format = str(job.arguments.get("output_format") or "png")
    source = _load_parent_image(job, library)
    if source is None:
        rng = _seed_rng(job.id, "upscale")
        source = _render_mesh_artwork(640, 640, rng=rng, transparent=False)

    max_edge = 2048
    scale = max(1.0, min(4.0, factor))
    target_w = int(source.width * scale)
    target_h = int(source.height * scale)
    longest = max(target_w, target_h)
    if longest > max_edge:
        ratio = max_edge / longest
        target_w = max(1, int(target_w * ratio))
        target_h = max(1, int(target_h * ratio))

    upscaled = source.resize((target_w, target_h), Image.Resampling.LANCZOS)
    upscaled = upscaled.filter(ImageFilter.UnsharpMask(radius=1.4, percent=125, threshold=2))
    data, content_type, ext = _encode_image(upscaled, output_format)
    return {
        "image": {
            "data": data,
            "content_type": content_type,
            "file_name": f"demo-upscaled{ext}",
            "width": upscaled.width,
            "height": upscaled.height,
        }
    }


def _sample_audio(job: Job) -> dict[str, Any]:
    """Generate a pleasant, valid playable WAV clip for demo speech jobs."""
    text = str(job.arguments.get("prompt") or job.arguments.get("text") or "Vertigo demo")
    is_gemini = "gemini" in job.endpoint.lower()
    sample_rate = 22050
    notes = [261.63, 329.63, 392.00, 523.25] if is_gemini else [220.00, 277.18, 329.63, 440.00]
    duration = min(4.0, max(1.8, 1.4 + len(text) / 90.0))
    total_samples = int(sample_rate * duration)
    note_len = max(1, total_samples // len(notes))

    frames = bytearray()
    for i in range(total_samples):
        t = i / sample_rate
        note_idx = min(len(notes) - 1, i // note_len)
        local_t = (i % note_len) / sample_rate
        note_dur = note_len / sample_rate
        freq = notes[note_idx]
        # Smooth attack/release envelope per note + gentle overall fade.
        attack = min(1.0, local_t / 0.04)
        release = min(1.0, max(0.0, (note_dur - local_t) / 0.12))
        env = attack * release * 0.28
        wave_val = (
            0.70 * math.sin(2 * math.pi * freq * t)
            + 0.22 * math.sin(2 * math.pi * freq * 2.0 * t)
            + 0.08 * math.sin(2 * math.pi * freq * 0.5 * t)
        )
        sample = max(-32767, min(32767, int(wave_val * env * 32767)))
        frames.extend(struct.pack("<h", sample))

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(frames))

    return {
        "audio": {
            "data": buf.getvalue(),
            "content_type": "audio/wav",
            "file_name": "demo-speech.wav",
        }
    }


def _sample_transcript(job: Job) -> dict[str, Any]:
    lang = str(job.arguments.get("language_code") or "eng")
    sentence = (
        "This is a sample transcript generated in Vertigo Demo Mode. "
        "Word-level timings are included so subtitle export works end to end."
    )
    tokens = sentence.split()
    words = []
    cursor = 0.0
    for idx, token in enumerate(tokens):
        dur = max(0.18, len(token) * 0.055)
        words.append(
            {
                "text": token,
                "start": round(cursor, 2),
                "end": round(cursor + dur, 2),
                "type": "word",
            }
        )
        cursor += dur + 0.06
        if idx < len(tokens) - 1:
            words.append({"text": " ", "start": round(cursor - 0.06, 2), "end": round(cursor, 2), "type": "spacing"})
    return {
        "text": sentence,
        "language_code": lang,
        "language_probability": 0.99,
        "words": words,
    }


def build_sample_result(job: Job, library: Library) -> dict[str, Any]:
    """Dispatch sample output generation by job kind."""
    if job.kind == "vector":
        return _sample_svg(job)
    if job.kind == "remove_bg":
        return _sample_remove_bg(job, library)
    if job.kind == "upscale":
        return _sample_upscale(job, library)
    if job.kind == "tts":
        return _sample_audio(job)
    if job.kind == "stt":
        return _sample_transcript(job)
    return _sample_images(job, library)
