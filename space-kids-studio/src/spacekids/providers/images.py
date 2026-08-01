"""Görsel sağlayıcıları.

İki sağlayıcı var:

* `NasaImageProvider` — NASA'nın açık görsel arşivinden gerçek uzay fotoğrafı
  indirir. Arşiv kamu malı olduğu için telif derdi yok; anahtar da gerekmez.
* `ProceduralImageProvider` — Pillow ile yıldız alanı üretir. İnternet yokken,
  testlerde ve NASA aramasının boş döndüğü durumlarda devreye girer. Aynı
  sorgu için daima aynı görseli üretir (deterministik).

`FallbackImageProvider` ikisini zincirler: önce gerçek fotoğraf, olmazsa üretim.
"""

from __future__ import annotations

import colorsys
import random
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter

from ..models import Scene
from .base import ImageProvider, ProviderError

NASA_SEARCH_URL = "https://images-api.nasa.gov/search"

#: Ken Burns efektinin yakınlaşırken piksel tüketmesi için hedeften büyük üretiyoruz.
DEFAULT_SIZE = (2560, 1440)

#: Tercih sırası: orijinal çok büyük olabilir, "large" genelde ideal.
_ASSET_PREFERENCE = ("~large.jpg", "~medium.jpg", "~orig.jpg", "~small.jpg")


def normalize(image: Image.Image, size: tuple[int, int] = DEFAULT_SIZE) -> Image.Image:
    """Görseli hedef orana göre ortadan kırpıp yeniden boyutlandırır."""
    target_w, target_h = size
    image = image.convert("RGB")
    source_ratio = image.width / image.height
    target_ratio = target_w / target_h

    if source_ratio > target_ratio:
        # Kaynak daha geniş: yanlardan kırp.
        new_width = round(image.height * target_ratio)
        left = (image.width - new_width) // 2
        image = image.crop((left, 0, left + new_width, image.height))
    else:
        # Kaynak daha uzun: üstten/alttan kırp.
        new_height = round(image.width / target_ratio)
        top = (image.height - new_height) // 2
        image = image.crop((0, top, image.width, top + new_height))

    return image.resize(size, Image.LANCZOS)


class ProceduralImageProvider:
    """Yıldız alanı + bulutsu + gezegen üreten yerel sağlayıcı.

    Sorgu metnini tohum olarak kullanır, böylece her sahne farklı ama
    tekrarlanabilir bir görsel alır.
    """

    name = "procedural"

    def __init__(self, size: tuple[int, int] = DEFAULT_SIZE) -> None:
        self.size = size

    def fetch(self, scene: Scene, destination: Path) -> Path:
        if destination.is_file():
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        image = self.render(scene.visual.query)
        image.save(destination, "JPEG", quality=90)
        return destination

    def render(self, seed_text: str) -> Image.Image:
        width, height = self.size
        rng = random.Random(seed_text)

        # Derin uzay için koyu bir dikey gradyan.
        base_hue = rng.uniform(0.55, 0.75)  # mavi–mor aralığı
        image = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(image)
        for y in range(height):
            t = y / height
            value = 0.05 + 0.10 * (1 - t)
            r, g, b = colorsys.hsv_to_rgb(base_hue, 0.75, value)
            draw.line([(0, y), (width, y)], fill=(int(r * 255), int(g * 255), int(b * 255)))

        # Yumuşak bulutsu lekeleri.
        nebula = Image.new("RGB", (width, height), (0, 0, 0))
        nebula_draw = ImageDraw.Draw(nebula)
        for _ in range(rng.randint(3, 5)):
            cx = rng.uniform(0, width)
            cy = rng.uniform(0, height)
            radius = rng.uniform(width * 0.15, width * 0.35)
            hue = (base_hue + rng.uniform(-0.15, 0.15)) % 1.0
            r, g, b = colorsys.hsv_to_rgb(hue, 0.65, rng.uniform(0.25, 0.45))
            nebula_draw.ellipse(
                [cx - radius, cy - radius, cx + radius, cy + radius],
                fill=(int(r * 255), int(g * 255), int(b * 255)),
            )
        nebula = nebula.filter(ImageFilter.GaussianBlur(radius=width // 12))
        image = Image.blend(image, nebula, alpha=0.45)

        draw = ImageDraw.Draw(image)

        # Yıldızlar: çoğu küçük, azı parlak.
        for _ in range(rng.randint(700, 1000)):
            x = rng.uniform(0, width)
            y = rng.uniform(0, height)
            brightness = rng.random()
            size = 1 if brightness < 0.85 else rng.uniform(1.5, 3.0)
            level = int(150 + 105 * brightness)
            draw.ellipse([x - size, y - size, x + size, y + size], fill=(level, level, min(255, level + 15)))

        # Bir gezegen: basit küre gölgelendirmesi.
        planet_radius = rng.uniform(height * 0.18, height * 0.30)
        px = rng.uniform(planet_radius, width - planet_radius)
        py = rng.uniform(planet_radius, height - planet_radius)
        planet_hue = (base_hue + rng.uniform(0.25, 0.55)) % 1.0
        steps = 48
        for step in range(steps, 0, -1):
            ratio = step / steps
            radius = planet_radius * ratio
            # Işık sol üstten geliyormuş gibi merkezi kaydır.
            offset = planet_radius * (1 - ratio) * 0.45
            r, g, b = colorsys.hsv_to_rgb(planet_hue, 0.55, 0.25 + 0.55 * (1 - ratio))
            draw.ellipse(
                [
                    px - radius - offset,
                    py - radius - offset,
                    px + radius - offset,
                    py + radius - offset,
                ],
                fill=(int(r * 255), int(g * 255), int(b * 255)),
            )

        return image.filter(ImageFilter.SMOOTH)


class NasaImageProvider:
    """NASA Image and Video Library'den görsel indirir."""

    name = "nasa"

    def __init__(
        self,
        size: tuple[int, int] = DEFAULT_SIZE,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.size = size
        self.timeout = timeout
        self._client = client

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, follow_redirects=True)
        return self._client

    def fetch(self, scene: Scene, destination: Path) -> Path:
        if destination.is_file():
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)

        last_error: Exception | None = None
        for query in scene.visual.queries:
            try:
                url = self._find_image_url(query)
            except Exception as error:  # ağ hatası: sıradaki sorguyu dene
                last_error = error
                continue
            if not url:
                continue
            try:
                image = self._download(url)
            except Exception as error:
                last_error = error
                continue
            normalize(image, self.size).save(destination, "JPEG", quality=88)
            return destination

        raise ProviderError(
            f"{scene.id}: NASA arşivinde görsel bulunamadı "
            f"(denenen sorgular: {', '.join(scene.visual.queries)})"
        ) from last_error

    def _find_image_url(self, query: str) -> str | None:
        client = self._get_client()
        response = client.get(
            NASA_SEARCH_URL, params={"q": query, "media_type": "image"}
        )
        response.raise_for_status()
        items = response.json().get("collection", {}).get("items") or []
        for item in items[:5]:
            asset_url = self._pick_asset(item)
            if asset_url:
                return asset_url
        return None

    def _pick_asset(self, item: dict) -> str | None:
        """Bir arama sonucundan en uygun çözünürlükteki dosyayı seçer."""
        collection_url = item.get("href")
        if collection_url:
            try:
                response = self._get_client().get(collection_url)
                response.raise_for_status()
                files = [str(path) for path in response.json()]
                for suffix in _ASSET_PREFERENCE:
                    for path in files:
                        if path.endswith(suffix):
                            return path
            except Exception:
                pass  # varlık listesi alınamadı; önizleme bağlantısına düş

        for link in item.get("links") or []:
            href = link.get("href")
            if href and link.get("render") == "image":
                return href
        return None

    def _download(self, url: str) -> Image.Image:
        import io

        response = self._get_client().get(url)
        response.raise_for_status()
        return Image.open(io.BytesIO(response.content))


class FallbackImageProvider:
    """Önce birincil sağlayıcıyı dener, hata alırsa yedeğe düşer."""

    def __init__(self, primary: ImageProvider, fallback: ImageProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}+{fallback.name}"
        #: Yedeğe düşülen sahneler — pipeline raporunda gösterilir.
        self.fallback_scenes: list[str] = []

    def fetch(self, scene: Scene, destination: Path) -> Path:
        try:
            return self.primary.fetch(scene, destination)
        except Exception:
            self.fallback_scenes.append(scene.id)
            return self.fallback.fetch(scene, destination)
