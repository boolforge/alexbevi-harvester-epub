"""
Binary asset handling: downloading and validating images, extracting video
poster frames, and generating the cover.

Nothing here touches ebooklib directly -- functions hand back plain
(filename, media_type, bytes) tuples that epub_build.py registers on the
book. Keeping "fetch and prepare bytes" separate from "wire bytes into an
EpubBook" makes both halves easier to reason about and test in isolation.
"""
from __future__ import annotations

import io
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config
from .source import SourceError, fetch_asset

log = logging.getLogger("harvester_epub.media")

_EXT_TO_MEDIA_TYPE = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


@dataclass
class PreparedAsset:
    filename: str
    media_type: str
    data: bytes


def _sniff_extension(url: str, data: bytes) -> str:
    """
    Prefer sniffing actual image bytes over trusting the URL's extension.
    A mislabeled extension is a real (if unusual) way to end up with a
    manifest entry whose declared media type doesn't match its content --
    exactly the kind of mismatch epubcheck flags as a hard error.
    """
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "").lower()
            if fmt == "jpeg":
                return "jpg"
            if fmt in ("png", "gif", "webp"):
                return fmt
    except Exception:
        pass
    ext = url.rsplit(".", 1)[-1].lower().split("?")[0]
    return ext if ext in _EXT_TO_MEDIA_TYPE else "png"


def prepare_image(node_id: str, source_path: str) -> PreparedAsset | None:
    """
    Download and validate one image. Returns None (never raises) on
    failure: a single missing screenshot should degrade that one figure,
    not abort the entire build. Caller is responsible for logging/counting.
    """
    try:
        data = fetch_asset(source_path)
    except SourceError as exc:
        log.warning("Image %s unavailable, skipping: %s", node_id, exc)
        return None

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
    except Exception as exc:
        log.warning("Image %s failed integrity check, skipping: %s", node_id, exc)
        return None

    ext = _sniff_extension(source_path, data)
    return PreparedAsset(
        filename=f"{node_id}.{ext}", media_type=_EXT_TO_MEDIA_TYPE[ext], data=data
    )


def prepare_video_poster(node_id: str, source_path: str, watch_url: str) -> PreparedAsset | None:
    """
    Download the referenced video and extract a representative frame with
    ffmpeg, then stamp a play-button glyph onto it so it reads unambiguously
    as "this used to be a video" rather than just an odd screenshot.

    Falls back to a plain generated placeholder card (no ffmpeg required)
    if the download or extraction fails for any reason -- video playback
    is not something any mainstream EPUB reading system supports anyway,
    so this is inherently a best-effort visual, never a build-critical one.
    """
    try:
        video_bytes = fetch_asset(source_path)
    except SourceError as exc:
        log.warning("Video %s unavailable (%s); using placeholder card", node_id, exc)
        return _build_video_placeholder(node_id)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / "clip.mp4"
            video_path.write_bytes(video_bytes)
            frame_path = Path(tmp) / "frame.png"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", "1", "-i", str(video_path),
                    "-frames:v", "1", "-vf", "scale=800:-1",
                    str(frame_path),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            frame = Image.open(frame_path).convert("RGB")
    except Exception as exc:
        log.warning("Poster extraction failed for %s (%s); using placeholder card", node_id, exc)
        return _build_video_placeholder(node_id)

    frame = _stamp_play_button(frame)
    buf = io.BytesIO()
    frame.save(buf, format="PNG")
    return PreparedAsset(filename=f"{node_id}.png", media_type="image/png", data=buf.getvalue())


def _stamp_play_button(frame: Image.Image) -> Image.Image:
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = frame.size
    r = min(w, h) // 9
    cx, cy = w // 2, h // 2
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(23, 21, 26, 190))
    tri = int(r * 0.9)
    draw.polygon(
        [(cx - tri * 0.35, cy - tri * 0.55), (cx - tri * 0.35, cy + tri * 0.55), (cx + tri * 0.6, cy)],
        fill=(232, 163, 61, 255),  # amber
    )
    return Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")


def _build_video_placeholder(node_id: str) -> PreparedAsset:
    w, h = 800, 450
    img = Image.new("RGB", (w, h), config.PALETTE["bg_raised"])
    draw = ImageDraw.Draw(img)
    _draw_hairline_border(draw, w, h)
    frame = _stamp_play_button(img)
    try:
        font = ImageFont.truetype(config.FONTS["mono"], 22)
        label = "VIDEO"
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) / 2, h - 60), label, font=font, fill=config.PALETTE["ink_muted"])
    except Exception:
        pass
    buf = io.BytesIO()
    frame.save(buf, format="PNG")
    return PreparedAsset(filename=f"{node_id}.png", media_type="image/png", data=buf.getvalue())


def _draw_hairline_border(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    draw.rectangle((0, 0, w - 1, h - 1), outline=config.PALETTE["rule"], width=2)


# --- Cover -------------------------------------------------------------

CHAPTER_LABELS = [
    "Ghidra & the DOS Extender",
    "First Contact",
    "File Formats",
    "Command Opcodes",
    "Debugging Audio",
    "Timers",
    "Demo Support",
]


def build_cover(width: int = 1600, height: int = 2400) -> bytes:
    """
    Generate cover art in the book's own visual language rather than reuse
    the blog's repeated banner image (identical across all 7 posts, so it
    carries no real "this is the book" identity) or a stock template.

    Design: a faint full-height hex-dump texture -- a real structural motif
    from the subject matter (disassembly listings), not decoration for its
    own sake -- title set in IBM Plex Mono to read as "field notes," a
    compact manifest of the seven parts standing in for conventional cover
    copy, and a teal/amber two-color accent system instead of a single
    neon-on-black terminal cliche. Layout runs off an accumulating y cursor
    rather than independent fractions of the canvas, so sections can't
    silently overlap or leave dead space as text is added or trimmed.
    """
    bg = _hex(config.PALETTE["bg"])
    img = Image.new("RGB", (width, height), bg)
    _draw_hexdump_texture(img, width, height)
    draw = ImageDraw.Draw(img)

    margin = int(width * 0.09)
    content_w = width - 2 * margin
    ink, ink_muted = _hex(config.PALETTE["ink"]), _hex(config.PALETTE["ink_muted"])
    amber, teal, rule = _hex(config.PALETTE["amber"]), _hex(config.PALETTE["teal"]), _hex(config.PALETTE["rule"])

    mono_bold = ImageFont.truetype(config.FONTS["mono_bold"], int(width * 0.052))
    mono = ImageFont.truetype(config.FONTS["mono"], int(width * 0.026))
    mono_small = ImageFont.truetype(config.FONTS["mono"], int(width * 0.0225))
    mono_medium = ImageFont.truetype(config.FONTS["mono_medium"], int(width * 0.030))
    serif_italic = ImageFont.truetype(config.FONTS["serif_italic"], int(width * 0.030))

    y = int(height * 0.305)
    draw.text((margin, y), "0x00 // FIELD LOG", font=mono, fill=teal)
    y += int(width * 0.048)
    draw.line((margin, y, width - margin, y), fill=rule, width=2)
    y += int(width * 0.05)

    for line in _wrap_text(draw, "Reverse Engineering\nHarvester", mono_bold, content_w):
        draw.text((margin, y), line, font=mono_bold, fill=ink)
        y += int(width * 0.062)

    y += int(width * 0.006)
    draw.text((margin, y), "with Ghidra and Codex", font=mono_medium, fill=amber)
    y += int(width * 0.075)

    draw.line((margin, y, width - margin, y), fill=rule, width=1)
    y += int(width * 0.045)

    # A compact manifest of the seven parts stands in for conventional
    # cover copy: it's real information (the actual chapter sequence)
    # rather than pure decoration, and it fills the space between title
    # and colophon-style footer that would otherwise sit empty.
    for i, label in enumerate(CHAPTER_LABELS, start=1):
        marker = f"0x{i:02d}"
        draw.text((margin, y), marker, font=mono_small, fill=teal)
        draw.text((margin + int(width * 0.075), y), label, font=mono_small, fill=ink_muted)
        y += int(width * 0.0375)

    y += int(width * 0.035)
    draw.line((margin, y, width - margin, y), fill=rule, width=1)

    footer_y = height - int(height * 0.115)
    draw.text((margin, footer_y), "A Seven-Part Field Log", font=serif_italic, fill=ink_muted)
    draw.text((margin, footer_y + int(width * 0.046)), "Alex Bevilacqua", font=mono_medium, fill=ink)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _draw_hexdump_texture(img: Image.Image, width: int, height: int) -> None:
    """
    A faint hex-offset texture across the full cover, evoking a disassembly
    listing, strongest near the top (where it stands alone) and left flat
    and low-alpha everywhere else so it reads as background wash rather
    than competing with the title and manifest text placed on top of it.
    """
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.truetype(config.FONTS["mono"], int(width * 0.018))
    rule = _hex(config.PALETTE["rule"])
    row_h = int(height * 0.026)
    top_band_rows = int(height * 0.20 / row_h)
    addr = 0x4010
    for row in range(int(height / row_h) + 1):
        alpha = 150 if row < top_band_rows else 18
        chunk = " ".join(f"{(addr + k * 4) & 0xFFFF:04X}" for k in range(10))
        draw.text((int(width * 0.09), row * row_h), chunk, font=font, fill=(*rule, alpha))
        addr += 0x40
    composited = Image.alpha_composite(img.convert("RGBA"), overlay)
    img.paste(composited.convert("RGB"), (0, 0))


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def _hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
