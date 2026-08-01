"""Seedance (ByteDance) AI video klibi sağlayıcısı.

**Neden kotalı ve isteğe bağlı:** Seedance klip başına 5-10 saniye üretir.
5 dakikalık bir bölümü tamamen bu şekilde üretmek 30-60 klip demek — ne
ücretsiz kotaya sığar ne de bütçeye. Bu yüzden yalnızca `hero` işaretli
"yıldız sahneler" için, bölüm başına sıkı bir tavan altında kullanılır.
Videonun omurgası NASA görselleri + Ken Burns olmaya devam eder.

**Asla pipeline'ı kırmaz:** Kota dolduğunda, anahtar olmadığında veya servis
hata verdiğinde `None` döner ve o sahne durağan görsele geri düşer.

İki erişim yolu desteklenir:

* ``byteplus`` — ByteDance'in resmi ModelArk API'si (kayıtta ücretsiz token).
* ``fal`` — fal.ai üzerinden üçüncü parti erişim.

> **Not:** İstek/yanıt şekilleri sağlayıcıların belgelenmiş iş akışına göre
> yazıldı, ancak canlı bir anahtarla doğrulanmadı — bu depoda anahtar yok.
> Anahtarını taktığında bir uyumsuzluk çıkarsa `_SUBMIT_ENDPOINT`/
> `_extract_video_url` noktalarını ayarlaman yeterli; geri kalan akış aynı.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from ..config import SeedanceSettings
from ..models import Scene

logger = logging.getLogger(__name__)

#: Görev tamamlanana kadar bekleme aralığı (saniye).
POLL_INTERVAL = 5.0

_DEFAULT_BASE_URLS = {
    "byteplus": "https://ark.ap-southeast.bytepluses.com/api/v3",
    "fal": "https://queue.fal.run",
}


class SeedanceClipProvider:
    """Kota korumalı AI video klibi üreteci."""

    name = "seedance"

    def __init__(
        self,
        settings: SeedanceSettings,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        #: Bu bölümde şimdiye kadar üretilen klip sayısı (bütçe koruması).
        self.clips_generated = 0
        #: Neden klip üretilemediğini raporlamak için.
        self.skipped: list[str] = []

    # --- kota ve uygunluk ------------------------------------------------

    @property
    def quota_remaining(self) -> int:
        return max(self.settings.max_clips_per_episode - self.clips_generated, 0)

    def is_available(self) -> bool:
        return bool(self.settings.enabled and self.settings.api_key)

    def _should_generate(self, scene: Scene) -> bool:
        if not self.is_available():
            return False
        if not scene.visual.hero or not scene.visual.clip_prompt:
            return False
        if self.quota_remaining <= 0:
            self.skipped.append(f"{scene.id}: bölüm kotası doldu")
            return False
        return True

    # --- üretim ----------------------------------------------------------

    def generate(self, scene: Scene, destination: Path) -> Path | None:
        # Önceden üretilmiş klip kotadan düşmez — yeniden çalıştırma ücretsizdir.
        if destination.is_file() and destination.stat().st_size > 0:
            return destination

        if not self._should_generate(scene):
            return None

        prompt = self._build_prompt(scene)
        try:
            task_id = self._submit(prompt)
            video_url = self._await_result(task_id)
            if not video_url:
                self.skipped.append(f"{scene.id}: görev sonuç üretmedi")
                return None
            self._download(video_url, destination)
        except Exception as error:
            # AI klip her zaman isteğe bağlı: hata pipeline'ı durdurmaz.
            logger.warning("Seedance klibi üretilemedi (%s): %s", scene.id, error)
            self.skipped.append(f"{scene.id}: {error}")
            destination.unlink(missing_ok=True)
            return None

        self.clips_generated += 1
        return destination

    def _build_prompt(self, scene: Scene) -> str:
        base = (scene.visual.clip_prompt or "").strip()
        # Çocuk içeriği için tonu her istekte yeniden sabitliyoruz.
        return (
            f"{base}. Gentle camera motion, soft lighting, cheerful and calm mood, "
            "suitable for young children, no text, no people speaking."
        )

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.settings.timeout, follow_redirects=True
            )
        return self._client

    def _base_url(self) -> str:
        return (
            self.settings.base_url
            or _DEFAULT_BASE_URLS.get(self.settings.provider)
            or _DEFAULT_BASE_URLS["byteplus"]
        )

    def _headers(self) -> dict[str, str]:
        if self.settings.provider == "fal":
            return {"Authorization": f"Key {self.settings.api_key}"}
        return {"Authorization": f"Bearer {self.settings.api_key}"}

    def _submit(self, prompt: str) -> str:
        client = self._get_client()
        base = self._base_url().rstrip("/")

        if self.settings.provider == "fal":
            response = client.post(
                f"{base}/fal-ai/{self.settings.model}",
                headers=self._headers(),
                json={
                    "prompt": prompt,
                    "duration": self.settings.clip_seconds,
                    "resolution": "720p",
                },
            )
            response.raise_for_status()
            payload = response.json()
            task_id = payload.get("request_id")
        else:
            response = client.post(
                f"{base}/contents/generations/tasks",
                headers=self._headers(),
                json={
                    "model": self.settings.model,
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"{prompt} --resolution 720p "
                                f"--duration {self.settings.clip_seconds}"
                            ),
                        }
                    ],
                },
            )
            response.raise_for_status()
            task_id = response.json().get("id")

        if not task_id:
            raise RuntimeError("görev kimliği alınamadı")
        return str(task_id)

    def _await_result(self, task_id: str) -> str | None:
        client = self._get_client()
        base = self._base_url().rstrip("/")
        deadline = time.monotonic() + self.settings.timeout

        if self.settings.provider == "fal":
            status_url = f"{base}/fal-ai/{self.settings.model}/requests/{task_id}/status"
            result_url = f"{base}/fal-ai/{self.settings.model}/requests/{task_id}"
        else:
            status_url = f"{base}/contents/generations/tasks/{task_id}"
            result_url = status_url

        while time.monotonic() < deadline:
            response = client.get(status_url, headers=self._headers())
            response.raise_for_status()
            payload = response.json()
            status = str(payload.get("status", "")).lower()

            if status in {"succeeded", "completed", "ok"}:
                if result_url != status_url:
                    final = client.get(result_url, headers=self._headers())
                    final.raise_for_status()
                    payload = final.json()
                return _extract_video_url(payload)
            if status in {"failed", "error", "cancelled"}:
                raise RuntimeError(f"görev başarısız: {payload.get('error') or status}")

            time.sleep(POLL_INTERVAL)

        raise TimeoutError(f"görev {self.settings.timeout:.0f} saniyede tamamlanmadı")

    def _download(self, url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        client = self._get_client()
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        return destination


def _extract_video_url(payload: dict) -> str | None:
    """Yanıttaki video bağlantısını bulur.

    Sağlayıcılar arasında yerleşim değiştiği için birkaç bilinen konumu
    sırayla deniyoruz.
    """
    content = payload.get("content")
    if isinstance(content, dict) and content.get("video_url"):
        return str(content["video_url"])

    video = payload.get("video")
    if isinstance(video, dict) and video.get("url"):
        return str(video["url"])

    for key in ("video_url", "url", "output_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value

    return None


class NullClipProvider:
    """AI klip üretmeyen sağlayıcı — varsayılan davranış."""

    name = "none"

    def generate(self, scene: Scene, destination: Path) -> Path | None:
        return None
