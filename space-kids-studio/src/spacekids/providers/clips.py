"""AI video klibi sağlayıcısı (Seedance ve benzerleri).

Wire formatı `config/clip_providers.yaml` içindeki profillerden okunur —
bkz. `clip_profiles.py`. Bu modül akışı yürütür:

    istem süzgeci → görev gönder → durumu bekle → videoyu indir

**Üç koruma her zaman devrede:**

* **Kota** — bölüm başına klip tavanı (varsayılan 3). Seedance klip başına
  5-10 saniye üretir; tüm videoyu böyle üretmek ücretsiz kotayı da bütçeyi de
  aşar. Klipler yalnızca `hero` işaretli sahnelerde kullanılır.
* **İstem süzgeci** — telifli karakter/marka içeren istemler gönderilmez.
* **Asla kırmama** — hata, kota veya anahtar yokluğu `None` döndürür ve o
  sahne durağan görsele geri düşer. AI klip daima isteğe bağlıdır.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..config import ClipSettings
from ..models import Scene
from .clip_profiles import ClipProviderProfile, ProfileError, load_clip_providers, render
from .clip_safety import ClipPromptRejected, guard_prompt

logger = logging.getLogger(__name__)

#: Görev durumu sorgulama aralığı (saniye).
POLL_INTERVAL = 5.0


@dataclass
class ClipRequest:
    """Bir sahne için gönderilecek isteğin özeti — `--dry-run` bunu gösterir."""

    scene_id: str
    provider: str
    model: str
    url: str
    body: dict[str, Any]
    prompt: str


class ClipProvider:
    """Profil odaklı AI klip üreteci."""

    def __init__(
        self,
        settings: ClipSettings,
        profile: ClipProviderProfile,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.profile = profile
        self.name = f"clip:{profile.id}"
        self._client = client
        #: Bu bölümde üretilen klip sayısı (kota takibi).
        self.clips_generated = 0
        #: Neden klip üretilemediği — CLI raporunda gösterilir.
        self.skipped: list[str] = []

    # --- kurulum ---------------------------------------------------------

    @classmethod
    def from_settings(
        cls,
        settings: ClipSettings,
        providers_file: Path,
        client: httpx.Client | None = None,
    ) -> ClipProvider:
        library = load_clip_providers(providers_file)
        return cls(settings, library.get(settings.provider), client=client)

    @property
    def model(self) -> str:
        return self.settings.model or self.profile.default_model

    @property
    def quota_remaining(self) -> int:
        return max(self.settings.max_clips_per_episode - self.clips_generated, 0)

    def is_available(self) -> bool:
        return bool(self.settings.enabled and self.settings.api_key)

    # --- istek hazırlama -------------------------------------------------

    def _context(self, prompt: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt": prompt,
            "duration": self.settings.clip_seconds,
            "resolution": self.settings.resolution,
        }

    def prepare(self, scene: Scene) -> ClipRequest:
        """Sahne için gönderilecek isteği kurar — ağa çıkmaz.

        `--dry-run` bunu kullanır: kota harcamadan tam olarak ne
        gönderileceğini görebilirsin.
        """
        prompt = guard_prompt(scene.visual.clip_prompt or "")
        context = self._context(prompt)
        return ClipRequest(
            scene_id=scene.id,
            provider=self.profile.id,
            model=self.model,
            url=self.profile.url(self.profile.submit.path, context),
            body=render(self.profile.submit.body, context),
            prompt=prompt,
        )

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

        try:
            request = self.prepare(scene)
        except ClipPromptRejected as error:
            # Süzgeç reddi sessiz geçilmez: kullanıcı bunu bilmeli.
            logger.warning("Klip istemi reddedildi (%s): %s", scene.id, error)
            self.skipped.append(f"{scene.id}: {error}")
            return None

        try:
            video_url = self._submit_and_wait(request)
            if not video_url:
                self.skipped.append(f"{scene.id}: sonuçta video bağlantısı yok")
                return None
            self._download(video_url, destination)
        except Exception as error:
            logger.warning("Klip üretilemedi (%s): %s", scene.id, error)
            self.skipped.append(f"{scene.id}: {error}")
            destination.unlink(missing_ok=True)
            return None

        self.clips_generated += 1
        return destination

    def _submit_and_wait(self, request: ClipRequest) -> str | None:
        profile = self.profile
        client = self._get_client()
        headers = profile.headers(self.settings.api_key or "")

        response = client.request(
            profile.submit.method, request.url, headers=headers, json=request.body
        )
        response.raise_for_status()
        payload = response.json()

        # Bazı servisler videoyu doğrudan döndürür; bekleme adımı yok.
        if profile.is_synchronous:
            return profile.first_value(payload, profile.submit.video_url_paths)

        task_id = profile.first_value(payload, profile.submit.task_id_paths)
        if not task_id:
            raise ProfileError(
                f"görev kimliği bulunamadı (denenen yollar: "
                f"{', '.join(profile.submit.task_id_paths)})"
            )
        return self._await_result(str(task_id), request.prompt)

    def _await_result(self, task_id: str, prompt: str) -> str | None:
        profile = self.profile
        assert profile.poll is not None and profile.result is not None
        client = self._get_client()
        headers = profile.headers(self.settings.api_key or "")
        context = {**self._context(prompt), "task_id": task_id}
        poll_url = profile.url(profile.poll.path, context)
        deadline = time.monotonic() + self.settings.timeout

        while True:
            response = client.get(poll_url, headers=headers)
            response.raise_for_status()
            payload = response.json()

            status = profile.first_value(payload, profile.poll.status_paths)
            status_text = str(status).lower() if status is not None else ""
            succeeded = {value.lower() for value in profile.poll.succeeded}
            failed = {value.lower() for value in profile.poll.failed}

            if status_text in succeeded:
                if profile.result.path:
                    result_url = profile.url(profile.result.path, context)
                    final = client.get(result_url, headers=headers)
                    final.raise_for_status()
                    payload = final.json()
                return profile.first_value(payload, profile.result.video_url_paths)

            if status_text in failed:
                reason = profile.first_value(payload, profile.poll.error_paths)
                raise ProfileError(f"görev başarısız: {reason or status_text}")

            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"görev {self.settings.timeout:.0f} saniyede tamamlanmadı "
                    f"(son durum: {status_text or 'bilinmiyor'})"
                )
            time.sleep(POLL_INTERVAL)

    def _download(self, url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        client = self._get_client()
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        if destination.stat().st_size == 0:
            destination.unlink(missing_ok=True)
            raise ProfileError("indirilen video boş")
        return destination

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.settings.timeout, follow_redirects=True
            )
        return self._client


class NullClipProvider:
    """AI klip üretmeyen sağlayıcı — varsayılan davranış."""

    name = "none"

    def __init__(self) -> None:
        self.clips_generated = 0
        self.skipped: list[str] = []

    def generate(self, scene: Scene, destination: Path) -> Path | None:
        return None
