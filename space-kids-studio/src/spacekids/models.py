"""Bölüm (episode) veri modelleri.

Bir bölüm iki katmandan oluşur:

* **Dilden bağımsız iskelet** — sahne sırası, her sahnenin görsel tarifi ve
  hareketi. Bu katman bir kez üretilir ve tüm diller aynı görselleri paylaşır.
* **Yerelleştirme** — sahne anlatımları, başlık, açıklama, etiketler. Her dil
  için ayrı tutulur; video da her dil için ayrı render edilir.

Bu ayrım sayesinde Türkçe ve İngilizce versiyonlar aynı görsel kurguyu
paylaşır, sadece ses ve metin değişir.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Desteklenen dil kodları. Yeni dil eklemek için burayı ve topics.yaml'ı genişletmek yeterli.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("tr", "en")

SCENE_ID_PATTERN = re.compile(r"^s\d{2,3}$")

#: YouTube başlık sınırı.
MAX_TITLE_LENGTH = 100
#: YouTube açıklama sınırı.
MAX_DESCRIPTION_LENGTH = 5000
#: YouTube, etiketlerin toplam karakter uzunluğunu 500 ile sınırlar.
MAX_TAGS_TOTAL_LENGTH = 500


class Motion(str, Enum):
    """Durağan bir görsele uygulanacak Ken Burns hareketi."""

    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"


class SceneKind(str, Enum):
    """Sahnenin anlatı içindeki rolü."""

    INTRO = "intro"
    FACT = "fact"
    QUESTION = "question"
    OUTRO = "outro"


class Visual(BaseModel):
    """Bir sahnenin görselinin nasıl bulunacağı/üretileceği.

    `query` daima İngilizce tutulur: NASA arşivi yalnızca İngilizce aranabiliyor
    ve görsel katmanı zaten diller arasında paylaşılıyor.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2)
    fallback_queries: list[str] = Field(default_factory=list)
    motion: Motion = Motion.ZOOM_IN
    #: True ise bu sahne, AI video klibi (Seedance) için aday "yıldız sahne"dir.
    hero: bool = False
    #: Seedance'e verilecek İngilizce sahne tarifi. Yalnızca hero sahnelerde anlamlı.
    clip_prompt: str | None = None

    @property
    def queries(self) -> list[str]:
        """Denenecek arama sorguları, öncelik sırasıyla."""
        return [self.query, *self.fallback_queries]


class Scene(BaseModel):
    """Tek bir sahne: bir görsel + her dilde bir anlatım metni."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: SceneKind = SceneKind.FACT
    visual: Visual
    #: dil kodu -> anlatım metni
    narration: dict[str, str]

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not SCENE_ID_PATTERN.match(value):
            raise ValueError(f"sahne id'si 's01' biçiminde olmalı, alınan: {value!r}")
        return value

    @field_validator("narration")
    @classmethod
    def _validate_narration(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("sahnenin en az bir dilde anlatımı olmalı")
        cleaned: dict[str, str] = {}
        for lang, text in value.items():
            stripped = " ".join(text.split())
            if not stripped:
                raise ValueError(f"{lang!r} dilindeki anlatım boş olamaz")
            cleaned[lang] = stripped
        return cleaned

    def text(self, language: str) -> str:
        """Verilen dildeki anlatımı döndürür."""
        try:
            return self.narration[language]
        except KeyError:
            raise KeyError(
                f"{self.id} sahnesinin {language!r} dilinde anlatımı yok"
            ) from None

    def word_count(self, language: str) -> int:
        return len(self.text(language).split())


class Localization(BaseModel):
    """Bir dile ait yayın metinleri."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_LENGTH)
    tags: list[str] = Field(default_factory=list)
    #: Kapak görselinde kullanılan kısa, çarpıcı metin.
    hook: str = Field(min_length=1, max_length=60)

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(tag.split()) for tag in value]
        cleaned = [tag for tag in cleaned if tag]
        # Sıra korunarak tekilleştirme (büyük/küçük harf duyarsız).
        seen: set[str] = set()
        unique: list[str] = []
        for tag in cleaned:
            key = tag.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(tag)
        total = sum(len(tag) for tag in unique) + max(len(unique) - 1, 0)
        if total > MAX_TAGS_TOTAL_LENGTH:
            raise ValueError(
                f"etiketlerin toplam uzunluğu {MAX_TAGS_TOTAL_LENGTH} karakteri aşıyor ({total})"
            )
        return unique


class Episode(BaseModel):
    """Üretilecek bölümün tam tanımı — render'ın tek girdisi."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    topic_id: str = Field(min_length=1)
    languages: list[str] = Field(min_length=1)
    age_min: int = Field(ge=2, le=12)
    age_max: int = Field(ge=2, le=16)
    scenes: list[Scene] = Field(min_length=2)
    #: dil kodu -> yayın metinleri
    localizations: dict[str, Localization]
    #: Senaryoyu hangi sağlayıcının ürettiği (izlenebilirlik için).
    generator: str = "unknown"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _validate_consistency(self) -> Episode:
        if self.age_max < self.age_min:
            raise ValueError("age_max, age_min'den küçük olamaz")

        duplicates = {lang for lang in self.languages if self.languages.count(lang) > 1}
        if duplicates:
            raise ValueError(f"languages listesinde tekrar eden dil(ler): {sorted(duplicates)}")

        scene_ids = [scene.id for scene in self.scenes]
        if len(set(scene_ids)) != len(scene_ids):
            raise ValueError("sahne id'leri benzersiz olmalı")

        for language in self.languages:
            if language not in self.localizations:
                raise ValueError(f"{language!r} dili için yerelleştirme (başlık/açıklama) eksik")
            missing = [s.id for s in self.scenes if language not in s.narration]
            if missing:
                raise ValueError(
                    f"{language!r} dilinde anlatımı olmayan sahneler: {', '.join(missing)}"
                )
        return self

    def scene(self, scene_id: str) -> Scene:
        for scene in self.scenes:
            if scene.id == scene_id:
                return scene
        raise KeyError(f"sahne bulunamadı: {scene_id}")

    def hero_scenes(self) -> list[Scene]:
        """AI video klibi için işaretlenmiş sahneler."""
        return [scene for scene in self.scenes if scene.visual.hero]

    def total_words(self, language: str) -> int:
        return sum(scene.word_count(language) for scene in self.scenes)


class SceneTiming(BaseModel):
    """Render sonrası bir sahnenin nihai zamanlaması (saniye)."""

    model_config = ConfigDict(extra="forbid")

    scene_id: str
    start: float = Field(ge=0)
    duration: float = Field(gt=0)

    @property
    def end(self) -> float:
        return self.start + self.duration


class RenderManifest(BaseModel):
    """Bir dil için render çıktılarının özeti."""

    model_config = ConfigDict(extra="forbid")

    episode_id: str
    language: str
    video_path: str
    subtitle_path: str
    thumbnail_path: str
    metadata_path: str
    duration: float = Field(gt=0)
    timings: list[SceneTiming]
    #: Gerçekten AI klip üretilen sahneler (Seedance açıkken dolar).
    ai_clip_scenes: list[str] = Field(default_factory=list)
    rendered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
