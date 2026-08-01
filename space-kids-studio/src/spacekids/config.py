"""Ayarlar.

Öncelik sırası: CLI bayrakları > ortam değişkenleri > `settings.toml` > varsayılanlar.

API anahtarları **yalnızca** ortam değişkenlerinden okunur; kazara commit'lenmesin
diye TOML dosyasından anahtar okumuyoruz.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .models import SUPPORTED_LANGUAGES

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent


class VideoSettings(BaseModel):
    """Çıktı videosunun teknik özellikleri."""

    model_config = ConfigDict(extra="forbid")

    width: int = 1920
    height: int = 1080
    fps: int = 30
    #: Sahne başına, anlatım sesine eklenen sessizlik payı (saniye).
    scene_padding: float = 0.6
    #: Anlatım susmuş olsa bile bir sahnenin görünür kalacağı asgari süre.
    min_scene_duration: float = 2.5
    #: Her sahnenin başındaki/sonundaki karartma süresi — sahne geçişini yumuşatır.
    fade_duration: float = 0.4
    #: Fon müziği dosyası (opsiyonel). Yoksa video sadece anlatım sesi taşır.
    music_path: Path | None = None
    #: Fon müziğinin anlatıma göre seviyesi (0-1).
    music_volume: float = 0.12


class SeedanceSettings(BaseModel):
    """AI video klibi (ByteDance Seedance) ayarları.

    Seedance klip başına 5-10 saniye üretir; tüm videoyu bu şekilde üretmek hem
    ücretsiz kotayı hem de bütçeyi aşar. Bu yüzden yalnızca `hero` işaretli
    sahnelerde ve sıkı bir kota altında kullanılır.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    #: "byteplus" (resmi ModelArk) veya "fal" (3. parti).
    provider: str = "byteplus"
    model: str = "seedance-2-0"
    #: Bölüm başına üretilecek azami klip sayısı — bütçe koruması.
    max_clips_per_episode: int = 3
    clip_seconds: int = 5
    api_key: str | None = None
    base_url: str | None = None
    timeout: float = 300.0


class Settings(BaseModel):
    """Uygulamanın tüm ayarları."""

    model_config = ConfigDict(extra="forbid")

    #: Bölüm klasörlerinin kök dizini.
    workspace: Path = PROJECT_ROOT / "episodes"
    topics_file: Path = PROJECT_ROOT / "config" / "topics.yaml"
    assets_dir: Path = PROJECT_ROOT / "assets"

    languages: list[str] = Field(default_factory=lambda: list(SUPPORTED_LANGUAGES))
    age_min: int = 4
    age_max: int = 8
    #: Bölümdeki bilgi sahnesi sayısı (intro/outro buna dahil değil).
    fact_scene_count: int = 6

    video: VideoSettings = Field(default_factory=VideoSettings)
    seedance: SeedanceSettings = Field(default_factory=SeedanceSettings)

    #: True ise hiçbir dış servise çıkılmaz: sesler sessiz, görseller prosedürel üretilir.
    offline: bool = False

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"

    #: edge-tts ses kimlikleri (dil -> ses adı).
    voices: dict[str, str] = Field(
        default_factory=lambda: {
            "tr": "tr-TR-EmelNeural",
            "en": "en-US-AnaNeural",
        }
    )

    @classmethod
    def load(cls, config_file: Path | None = None, **overrides: object) -> Settings:
        """Ayarları TOML + ortam değişkenleri + doğrudan geçilen değerlerle kurar."""
        data: dict[str, object] = {}

        candidate = config_file or (PROJECT_ROOT / "settings.toml")
        if candidate.is_file():
            with candidate.open("rb") as handle:
                data.update(tomllib.load(handle))

        env_key = os.environ.get("ANTHROPIC_API_KEY")
        if env_key:
            data["anthropic_api_key"] = env_key

        seedance = dict(data.get("seedance") or {})  # type: ignore[arg-type]
        seedance_key = os.environ.get("SEEDANCE_API_KEY") or os.environ.get(
            "BYTEPLUS_API_KEY"
        )
        if seedance_key:
            seedance["api_key"] = seedance_key
            seedance.setdefault("enabled", True)
        if seedance:
            data["seedance"] = seedance

        data.update({key: value for key, value in overrides.items() if value is not None})
        return cls.model_validate(data)

    def voice_for(self, language: str) -> str:
        """Dil için seslendirme kimliği; tanımsızsa İngilizce sese düşer."""
        return self.voices.get(language) or self.voices.get("en", "en-US-AnaNeural")
