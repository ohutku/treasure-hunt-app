"""NASA arşivinden hazır video klipleri.

Görsellerle aynı API, tek fark `media_type=video`. Arşiv **CC0 1.0 (kamu
malı)**: ticari kullanım serbest, atıf gerekmiyor, filigran yok, anahtar yok.

AI klip üretimine göre üç somut üstünlüğü var ve üçü de bu proje için önemli:

* **Telifli karakter riski sıfır.** Gerçek fırlatma görüntüsünde tanınabilir
  bir çizgi film karakteri çıkmaz — Seedance'i riskli yapan şey tam buydu.
* **Kalıcı bedava.** Kota yok, kredi yok, GPU yok.
* **Her sahnede kullanılabilir**, yalnızca `hero` sahnelerde değil; çünkü
  maliyeti olmayan bir kaynağı kısıtlamak için sebep yok.

Klibin kendi sesi yok sayılır: montaj yalnızca görüntüyü alır, ses daima
bizim anlatımımızdır.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..models import Scene

logger = logging.getLogger(__name__)

NASA_SEARCH_URL = "https://images-api.nasa.gov/search"

#: Varlık tercih sırası. `~orig` yayın kalitesinde master olabilir (yüzlerce MB);
#: 1080p çıktı için gereğinden büyük, bu yüzden sona konuyor.
_ASSET_PREFERENCE = ("~large.mp4", "~medium.mp4", "~small.mp4", "~orig.mp4", ".mp4")

#: İndirilecek azami dosya boyutu. Arşivdeki bazı masterlar GB'larca; sekiz
#: sahnelik bir bölüm bunu fark etmeden diski doldurabilir.
DEFAULT_MAX_BYTES = 200 * 1024 * 1024

#: Aramada bakılacak sonuç sayısı.
_MAX_ITEMS = 6


class NasaVideoError(RuntimeError):
    """Arşivden klip alınamadığında (çağıran tarafta hata değil, atlama sebebi)."""


@dataclass
class VideoCandidate:
    """Bir sahne için bulunan aday klip — `--dry-run` bunu gösterir."""

    scene_id: str
    query: str
    nasa_id: str
    title: str
    description: str
    url: str
    size_bytes: int | None = None

    @property
    def size_label(self) -> str:
        if not self.size_bytes:
            return "boyut bilinmiyor"
        return f"{self.size_bytes / 1024 / 1024:.1f} MB"


class NasaVideoProvider:
    """NASA arşivinden sahne kliplerini indirir.

    `ClipProvider` sözleşmesini uygular: klip bulunamazsa `None` döner ve
    pipeline o sahnede durağan görsel + Ken Burns'e geri düşer.
    """

    name = "nasa-video"

    def __init__(
        self,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.max_bytes = max_bytes
        self.timeout = timeout
        self._client = client
        #: Klip bulunamayan sahneler ve sebepleri — CLI raporunda gösterilir.
        self.skipped: list[str] = []
        self.clips_generated = 0

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, follow_redirects=True)
        return self._client

    # --- arama -----------------------------------------------------------

    def preview(self, scene: Scene) -> VideoCandidate | None:
        """Ağa çıkıp adayı bulur ama indirmez.

        `--dry-run` bunu kullanır: arşivde ne bulunduğunu (başlık ve açıklama
        dahil) indirmeden görebilirsin. Bu önemli, çünkü bir uzay sorgusu
        bazen basın toplantısı görüntüsü de döndürebiliyor.
        """
        for query in scene.visual.queries:
            try:
                candidate = self._search(scene.id, query)
            except Exception as error:
                logger.debug("NASA video araması başarısız (%s): %s", query, error)
                continue
            if candidate:
                return candidate
        return None

    def _search(self, scene_id: str, query: str) -> VideoCandidate | None:
        client = self._get_client()
        response = client.get(
            NASA_SEARCH_URL, params={"q": query, "media_type": "video"}
        )
        response.raise_for_status()
        items = response.json().get("collection", {}).get("items") or []

        for item in items[:_MAX_ITEMS]:
            asset_url = self._pick_asset(item)
            if not asset_url:
                continue
            data = (item.get("data") or [{}])[0]
            return VideoCandidate(
                scene_id=scene_id,
                query=query,
                nasa_id=str(data.get("nasa_id", "")),
                title=str(data.get("title", "")),
                description=" ".join(str(data.get("description", "")).split())[:200],
                url=asset_url,
                size_bytes=self._content_length(asset_url),
            )
        return None

    def _pick_asset(self, item: dict) -> str | None:
        """Bir arama sonucundan en uygun mp4'ü seçer."""
        collection_url = item.get("href")
        if not collection_url:
            return None
        try:
            response = self._get_client().get(collection_url)
            response.raise_for_status()
            files = [str(path) for path in response.json()]
        except Exception:
            return None

        # Altyazı/önizleme dosyaları da aynı listede; yalnızca video istiyoruz.
        videos = [path for path in files if path.lower().endswith(".mp4")]
        for suffix in _ASSET_PREFERENCE:
            for path in videos:
                if path.endswith(suffix):
                    return path
        return videos[0] if videos else None

    def _content_length(self, url: str) -> int | None:
        """Dosya boyutunu indirmeden öğrenmeye çalışır."""
        try:
            response = self._get_client().head(url)
            value = response.headers.get("content-length")
            return int(value) if value else None
        except Exception:
            return None

    # --- üretim ----------------------------------------------------------

    def generate(self, scene: Scene, destination: Path) -> Path | None:
        if destination.is_file() and destination.stat().st_size > 0:
            return destination

        candidate = None
        try:
            candidate = self.preview(scene)
        except Exception as error:
            self.skipped.append(f"{scene.id}: arama başarısız ({error})")
            return None

        if candidate is None:
            self.skipped.append(
                f"{scene.id}: arşivde video bulunamadı "
                f"({', '.join(scene.visual.queries)})"
            )
            return None

        if candidate.size_bytes and candidate.size_bytes > self.max_bytes:
            self.skipped.append(
                f"{scene.id}: klip çok büyük ({candidate.size_label}), atlandı"
            )
            return None

        try:
            self._download(candidate.url, destination)
        except Exception as error:
            logger.warning("NASA klibi indirilemedi (%s): %s", scene.id, error)
            self.skipped.append(f"{scene.id}: indirme başarısız ({error})")
            destination.unlink(missing_ok=True)
            return None

        self.clips_generated += 1
        return destination

    def _download(self, url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with self._get_client().stream("GET", url) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes():
                    written += len(chunk)
                    if written > self.max_bytes:
                        # Content-Length yoksa sınırı ancak burada uygulayabiliriz.
                        raise NasaVideoError(
                            f"klip {self.max_bytes / 1024 / 1024:.0f} MB sınırını aştı"
                        )
                    handle.write(chunk)

        if destination.stat().st_size == 0:
            destination.unlink(missing_ok=True)
            raise NasaVideoError("indirilen dosya boş")
        return destination
