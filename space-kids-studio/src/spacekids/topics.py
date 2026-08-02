"""Konu kütüphanesinin yüklenmesi ve doğrulanması."""

from __future__ import annotations

import random
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Motion


class TopicFact(BaseModel):
    """Bir bilgi kartı — doğrudan bir sahneye dönüşür."""

    model_config = ConfigDict(extra="forbid")

    #: NASA arşivinde aranacak İngilizce sorgu.
    visual: str = Field(min_length=2)
    motion: Motion = Motion.ZOOM_IN
    #: Dil kodu -> cümle. `visual`/`motion` dışındaki tüm anahtarlar dil kabul edilir.
    text: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _collect_languages(cls, data: object) -> object:
        """`tr:`/`en:` gibi serbest dil anahtarlarını `text` sözlüğüne toplar.

        YAML'da her bilgi kartını düz yazmak (visual/motion/tr/en) editörken çok
        daha rahat; iç modelde ise dilleri tek bir sözlükte tutmak istiyoruz.
        """
        if not isinstance(data, dict):
            return data
        reserved = {"visual", "motion", "text"}
        text = dict(data.get("text") or {})
        rest = {key: value for key, value in data.items() if key not in reserved}
        text.update(rest)
        kept = {key: value for key, value in data.items() if key in reserved}
        kept["text"] = text
        return kept

    @field_validator("text")
    @classmethod
    def _validate_text(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("bilgi kartının en az bir dilde metni olmalı")
        return {lang: " ".join(str(text).split()) for lang, text in value.items()}

    def languages(self) -> set[str]:
        return set(self.text)


class Topic(BaseModel):
    """Bir bölüm konusu."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    emoji: str = "🚀"
    title: dict[str, str]
    hook: dict[str, str]
    hero_prompt: str = ""
    keywords: dict[str, list[str]] = Field(default_factory=dict)
    facts: list[TopicFact] = Field(min_length=2)
    age_min: int | None = None
    age_max: int | None = None

    @model_validator(mode="after")
    def _validate_languages(self) -> Topic:
        title_langs = set(self.title)
        if not title_langs:
            raise ValueError(f"{self.id}: başlık en az bir dilde olmalı")
        missing_hook = title_langs - set(self.hook)
        if missing_hook:
            raise ValueError(
                f"{self.id}: şu dillerde 'hook' eksik: {sorted(missing_hook)}"
            )
        for index, fact in enumerate(self.facts, start=1):
            missing = title_langs - fact.languages()
            if missing:
                raise ValueError(
                    f"{self.id}: {index}. bilgi kartında şu diller eksik: {sorted(missing)}"
                )
        return self

    def languages(self) -> list[str]:
        return sorted(self.title)

    def keywords_for(self, language: str) -> list[str]:
        return self.keywords.get(language, [])


class TopicLibrary(BaseModel):
    """topics.yaml'ın tamamı."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    defaults: dict[str, int] = Field(default_factory=dict)
    topics: list[Topic] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> TopicLibrary:
        ids = [topic.id for topic in self.topics]
        duplicates = sorted({tid for tid in ids if ids.count(tid) > 1})
        if duplicates:
            raise ValueError(f"tekrar eden konu id'leri: {duplicates}")
        return self

    def get(self, topic_id: str) -> Topic:
        for topic in self.topics:
            if topic.id == topic_id:
                return topic
        available = ", ".join(topic.id for topic in self.topics)
        raise KeyError(f"konu bulunamadı: {topic_id!r}. Mevcut konular: {available}")

    def ids(self) -> list[str]:
        return [topic.id for topic in self.topics]

    def pick(self, *, exclude: set[str] | None = None, seed: int | None = None) -> Topic:
        """Kullanılmamış bir konu seçer; hepsi kullanıldıysa havuzu sıfırlar."""
        exclude = exclude or set()
        pool = [topic for topic in self.topics if topic.id not in exclude] or self.topics
        return random.Random(seed).choice(pool)


def load_topics(path: Path) -> TopicLibrary:
    """Konu kütüphanesini YAML dosyasından yükler ve doğrular."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"konu dosyası bulunamadı: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: kök seviyede bir sözlük bekleniyordu")
    return TopicLibrary.model_validate(raw)
