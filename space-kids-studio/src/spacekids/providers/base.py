"""Dış servislerin arkasındaki sözleşmeler.

Tüm dış bağımlılıklar (seslendirme, görsel, AI video klibi) bu protokollerin
arkasında durur. Sonuç:

* testler sahte sağlayıcılarla çalışır, ağ gerektirmez;
* `--offline` modda gerçek sağlayıcılar yerine yerel üretim devreye girer;
* yeni bir servis eklemek tek bir sınıf yazmak demektir.

Her sağlayıcı **idempotent** olmalı: hedef dosya zaten varsa yeniden üretmeden
onu döndürmeli. Pipeline'ın yarıda kalıp devam edebilmesi buna bağlı.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..models import Scene


class ProviderError(RuntimeError):
    """Bir sağlayıcı istenen çıktıyı üretemediğinde fırlatılır."""


@runtime_checkable
class VoiceProvider(Protocol):
    """Metni seslendirip ses dosyası üreten sağlayıcı."""

    name: str

    def synthesize(self, text: str, language: str, destination: Path) -> Path:
        """`text`i seslendirip `destination` yoluna yazar ve o yolu döndürür."""
        ...


@runtime_checkable
class ImageProvider(Protocol):
    """Sahne görselini bulan/üreten sağlayıcı."""

    name: str

    def fetch(self, scene: Scene, destination: Path) -> Path:
        """Sahneye uygun görseli `destination` yoluna yazar ve o yolu döndürür."""
        ...


@runtime_checkable
class ClipProvider(Protocol):
    """Sahne için kısa AI video klibi üreten sağlayıcı."""

    name: str

    def generate(self, scene: Scene, destination: Path) -> Path | None:
        """Klip üretir; kota dolduysa veya üretemezse None döner.

        None dönmek bir hata değildir — pipeline o sahne için durağan görsele
        geri düşer. Bu, AI klip üretiminin daima isteğe bağlı kalmasını sağlar.
        """
        ...
