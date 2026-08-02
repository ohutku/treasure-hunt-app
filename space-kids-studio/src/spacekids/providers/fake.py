"""Testler ve prova çalıştırmaları için sahte sağlayıcılar.

Gerçek sağlayıcılarla aynı sözleşmeyi uygularlar ama ağa çıkmaz, deterministik
davranır ve ne çağrıldıklarını kaydederler. Böylece pipeline'ın tamamı hiçbir
anahtar olmadan test edilebilir.
"""

from __future__ import annotations

from pathlib import Path

from ..media.ffmpeg import render_silence
from ..models import Scene
from .images import ProceduralImageProvider


class RecordingImageProvider:
    """Prosedürel görsel üretir ve hangi sahneler için çağrıldığını kaydeder."""

    name = "fake-image"

    def __init__(self, size: tuple[int, int] = (640, 360)) -> None:
        self._inner = ProceduralImageProvider(size=size)
        self.calls: list[str] = []

    def fetch(self, scene: Scene, destination: Path) -> Path:
        self.calls.append(scene.id)
        return self._inner.fetch(scene, destination)


class RecordingVoiceProvider:
    """Sabit süreli sessizlik üretir ve çağrıları kaydeder.

    Süre sabit olduğu için zamanlama testleri kesin sonuç verir.
    """

    name = "fake-voice"

    def __init__(self, duration: float = 1.0) -> None:
        self.duration = duration
        self.calls: list[tuple[str, int]] = []

    def synthesize(self, text: str, language: str, destination: Path) -> Path:
        self.calls.append((language, len(text)))
        if destination.is_file() and destination.stat().st_size > 0:
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        return render_silence(destination, self.duration)


class FailingVoiceProvider:
    """Daima hata veren sağlayıcı — yedeğe düşme davranışını test etmek için."""

    name = "failing-voice"

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, text: str, language: str, destination: Path) -> Path:
        self.calls += 1
        raise RuntimeError("sahte seslendirme hatası")


class FailingImageProvider:
    """Daima hata veren görsel sağlayıcısı."""

    name = "failing-image"

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, scene: Scene, destination: Path) -> Path:
        self.calls += 1
        raise RuntimeError("sahte görsel hatası")
