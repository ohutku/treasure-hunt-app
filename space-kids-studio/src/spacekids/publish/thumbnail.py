"""Kapak görseli (thumbnail) üretimi.

Bölümün bir sahnesinden alınan görselin üzerine, uzaktan da okunabilecek
büyüklükte bir kanca metni yerleştirir. Tıklanma oranını belirleyen şey
küçük ekranda okunabilirlik olduğu için:

* metin çok büyük ve kalın,
* arkasına koyu bir gradyan konur,
* harflerin etrafına kontur çizilir.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

#: YouTube'un önerdiği kapak boyutu.
THUMBNAIL_SIZE = (1280, 720)

#: Türkçe karakterleri de kapsayan, hemen her Linux dağıtımında bulunan fontlar.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    """Kalın bir sistem fontu yükler; bulunamazsa Pillow'un gömülü fontuna düşer."""
    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Metni verilen genişliğe sığacak satırlara böler."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def build_thumbnail(
    source_image: Path,
    hook: str,
    destination: Path,
    *,
    emoji: str = "",
    size: tuple[int, int] = THUMBNAIL_SIZE,
) -> Path:
    """Sahne görselinden kapak üretir."""
    width, height = size
    base = Image.open(source_image).convert("RGB")

    # Hedef orana kırp.
    target_ratio = width / height
    if base.width / base.height > target_ratio:
        new_width = round(base.height * target_ratio)
        left = (base.width - new_width) // 2
        base = base.crop((left, 0, left + new_width, base.height))
    else:
        new_height = round(base.width / target_ratio)
        top = (base.height - new_height) // 2
        base = base.crop((0, top, base.width, top + new_height))
    base = base.resize(size, Image.LANCZOS)

    # Metnin okunması için görseli biraz koyulaştır ve renkleri canlandır.
    base = ImageEnhance.Color(base).enhance(1.15)
    base = ImageEnhance.Brightness(base).enhance(0.82)

    # Alt yarıya koyu gradyan: metin daima okunur kalsın.
    overlay = Image.new("L", size, 0)
    overlay_draw = ImageDraw.Draw(overlay)
    for y in range(height):
        t = y / height
        overlay_draw.line([(0, y), (width, y)], fill=int(200 * max(t - 0.25, 0) ** 1.4))
    base = Image.composite(Image.new("RGB", size, (5, 8, 25)), base, overlay)

    draw = ImageDraw.Draw(base)
    text = f"{emoji} {hook}".strip() if emoji else hook

    # Metin kutuya sığana kadar punto küçült.
    margin = int(width * 0.06)
    max_text_width = width - margin * 2
    font_size = int(height * 0.20)
    while font_size > 24:
        font = load_font(font_size)
        lines = _wrap(text, font, max_text_width, draw)
        line_height = int(font_size * 1.15)
        if len(lines) <= 3 and len(lines) * line_height <= height * 0.55:
            break
        font_size -= 4
    else:  # pragma: no cover - aşırı uzun kancalarda son çare
        font = load_font(font_size)
        lines = _wrap(text, font, max_text_width, draw)
        line_height = int(font_size * 1.15)

    total_height = len(lines) * line_height
    y = height - margin - total_height
    stroke = max(font_size // 12, 3)

    for line in lines:
        line_width = draw.textlength(line, font=font)
        x = (width - line_width) / 2
        draw.text(
            (x, y),
            line,
            font=font,
            fill=(255, 255, 255),
            stroke_width=stroke,
            stroke_fill=(8, 12, 40),
        )
        y += line_height

    destination.parent.mkdir(parents=True, exist_ok=True)
    base.save(destination, "JPEG", quality=90)
    return destination
