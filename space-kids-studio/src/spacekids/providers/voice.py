"""Seslendirme sağlayıcıları.

* `EdgeVoiceProvider` — Microsoft Edge'in ücretsiz TTS servisini kullanır.
  Anahtar gerektirmez, Türkçe ve İngilizce'de doğal sesler verir.
* `SilentVoiceProvider` — metnin tahmini süresi kadar sessizlik üretir.
  İnternetsiz çalışmayı ve testleri mümkün kılar; video yine doğru uzunlukta
  çıkar, sadece ses olmaz.

`FallbackVoiceProvider` ikisini zincirler.
"""

from __future__ import annotations

import asyncio
import os
import ssl
from pathlib import Path

from ..media.ffmpeg import render_silence
from ..script.safety import estimate_duration
from .base import ProviderError, VoiceProvider

#: Sessiz ses üretilirken metnin tahmini süresine eklenen pay (saniye).
SILENCE_PADDING = 0.4

#: Ek CA paketinin okunacağı ortam değişkenleri, öncelik sırasıyla.
_CA_BUNDLE_ENV_VARS = ("SPACEKIDS_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


def configure_tls(module) -> bool:
    """edge-tts'in güven deposuna ortamdaki ek CA paketini ekler.

    edge-tts, SSL bağlamını `certifi` ile sabitler ve `SSL_CERT_FILE` gibi
    standart ortam değişkenlerini okumaz. TLS'i sonlandıran bir proxy arkasında
    çalışan herkes (kurumsal ağlar, CI kutuları) bu yüzden sertifika doğrulama
    hatası alır.

    Burada doğrulamayı **kapatmıyoruz** — yalnızca ortamda tanımlı CA'yı
    mevcut güven deposunun üzerine ekliyoruz. Ortamda böyle bir değişken yoksa
    hiçbir şey değişmez.
    """
    bundle = next(
        (os.environ[var] for var in _CA_BUNDLE_ENV_VARS if os.environ.get(var)), None
    )
    if not bundle or not Path(bundle).is_file():
        return False

    context = getattr(module, "_SSL_CTX", None)
    if not isinstance(context, ssl.SSLContext):
        return False

    try:
        context.load_verify_locations(cafile=bundle)
    except (OSError, ssl.SSLError):
        return False
    return True


class EdgeVoiceProvider:
    """edge-tts ile seslendirme."""

    name = "edge-tts"

    def __init__(self, voices: dict[str, str], rate: str = "-8%") -> None:
        #: Çocuk içeriği için biraz yavaşlatılmış konuşma daha anlaşılır oluyor.
        self.rate = rate
        self.voices = voices

    def synthesize(self, text: str, language: str, destination: Path) -> Path:
        if destination.is_file() and destination.stat().st_size > 0:
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)

        try:
            import edge_tts
            from edge_tts import communicate as _edge_communicate
        except ImportError as error:  # pragma: no cover - kurulum hatası
            raise ProviderError(
                "edge-tts kurulu değil: pip install -e '.[voice]'"
            ) from error

        configure_tls(_edge_communicate)

        voice = self.voices.get(language) or self.voices.get("en")
        if not voice:
            raise ProviderError(f"{language} dili için ses tanımlı değil")

        async def _run() -> None:
            communicate = edge_tts.Communicate(text, voice, rate=self.rate)
            await communicate.save(str(destination))

        try:
            asyncio.run(_run())
        except Exception as error:
            # Yarım kalan dosya bir sonraki çalıştırmayı yanıltmasın.
            destination.unlink(missing_ok=True)
            raise ProviderError(f"edge-tts seslendirmesi başarısız: {error}") from error

        if not destination.is_file() or destination.stat().st_size == 0:
            destination.unlink(missing_ok=True)
            raise ProviderError("edge-tts boş ses dosyası üretti")
        return destination


class SilentVoiceProvider:
    """Metnin tahmini süresi kadar sessizlik üretir."""

    name = "silent"

    def synthesize(self, text: str, language: str, destination: Path) -> Path:
        if destination.is_file() and destination.stat().st_size > 0:
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        duration = estimate_duration(text, language) + SILENCE_PADDING
        render_silence(destination, duration)
        return destination


class FallbackVoiceProvider:
    """Önce birincil sağlayıcıyı dener, hata alırsa yedeğe düşer."""

    def __init__(self, primary: VoiceProvider, fallback: VoiceProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}+{fallback.name}"
        #: Sessizliğe düşülen sahne/dil çiftleri — rapor için.
        self.fallback_count = 0

    def synthesize(self, text: str, language: str, destination: Path) -> Path:
        try:
            return self.primary.synthesize(text, language, destination)
        except Exception:
            self.fallback_count += 1
            return self.fallback.synthesize(text, language, destination)
