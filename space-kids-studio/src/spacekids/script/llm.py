"""Claude ile senaryo üretimi.

Şablon üretecinden farkı: bilgi kartlarını "doğruluk çıpası" olarak modele
verir ve modelden bunları çocuk diline çevirip akıcı bir anlatıya dönüştürmesini
ister. Böylece uydurma bilgi riski düşer, dil canlı olur.

İş bölümü bilinçli:

* **Model** yalnızca yaratıcı kısmı üretir — sahne anlatımları ve görsel
  tarifleri.
* **Şablonlar** başlık/açıklama/etiket üretmeye devam eder; bunlar YouTube
  sınırlarına uymak zorunda olan mekanik metinler ve deterministik olmaları
  daha değerli.

Üretilen senaryo güvenlik denetiminden geçirilir; hata varsa bulgular modele
geri verilip bir kez düzeltme istenir.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, create_model

from ..config import Settings
from ..models import Episode, Motion, Scene, SceneKind, Visual
from ..publish.metadata import build_localization
from ..topics import Topic
from .safety import LANGUAGE_PROFILES, SafetyReport, check_episode

if TYPE_CHECKING:  # pragma: no cover - yalnızca tip denetimi için
    from anthropic import Anthropic

#: Modelden istenecek düzeltme turu sayısı.
MAX_REPAIR_ROUNDS = 1

SYSTEM_PROMPT = """\
Sen okul öncesi çocuklar için eğitici uzay videoları yazan bir senaristsin.

Kurallar:
- Hedef kitle {age_min}-{age_max} yaş. Kelimeler basit, cümleler kısa olmalı.
- SADECE sana verilen bilgi kartlarındaki olguları kullan. Yeni bilimsel iddia
  uydurma. Kartları çocuk diline çevir, zenginleştir, ama olguyu değiştirme.
- Ton sıcak, meraklı ve sakin olsun. Çocuğu korkutacak ifade kullanma
  (ölüm, çarpışma, yok olma, tehlike gibi temalardan uzak dur).
- Ünlem işaretini abartma, bağırma. Clickbait yok.
- İzleyiciyi bir şey satın almaya veya bağlantıya tıklamaya yönlendirme.
- Her sahne tek bir fikir anlatsın.

Yapı:
- İlk sahne `intro`: çocuğu selamla, konuyu tanıt.
- Ortadaki sahneler `fact`: her biri bir bilgi kartına dayansın, kart sırasını koru.
- Son sahne `outro`: kısa bir özet ve sıcak bir veda.

Dil kuralları:
{language_rules}

Görsel tarifleri (`visual_query`) DAİMA İngilizce olmalı — NASA arşivinde
arama yapmak için kullanılacaklar. Somut, aranabilir ifadeler yaz
(örnek: "Saturn rings Cassini close up"), soyut tarifler yazma.
"""

USER_PROMPT = """\
Konu: {title}

Bilgi kartları (sırayla birer `fact` sahnesine dönüşecek):
{facts}

Bu konudan {scene_count} sahnelik bir bölüm senaryosu yaz.
"""

REPAIR_PROMPT = """\
Yazdığın senaryo denetimden geçemedi. Aşağıdaki sorunları düzelt ve
senaryonun tamamını yeniden üret. Yapıyı ve olguları koru, sadece sorunlu
yerleri onar.

Bulgular:
{issues}
"""


class LLMScriptGenerator:
    """Claude'dan senaryo isteyen üreteç."""

    name = "claude"

    def __init__(self, settings: Settings, client: "Anthropic | None" = None) -> None:
        self.settings = settings
        self._client = client

    def _get_client(self) -> "Anthropic":
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as error:  # pragma: no cover - kurulum hatası
            raise RuntimeError(
                "Claude ile senaryo üretmek için 'anthropic' paketi gerekli: "
                "pip install -e '.[llm]'"
            ) from error
        # API anahtarı verilmemişse SDK ortamdan (ANTHROPIC_API_KEY veya
        # 'ant auth login' profili) çözer.
        self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
        return self._client

    def generate(
        self,
        topic: Topic,
        *,
        episode_id: str,
        languages: list[str],
        age_min: int,
        age_max: int,
        fact_scene_count: int,
    ) -> Episode:
        languages = [lang for lang in languages if lang in topic.title]
        if not languages:
            raise ValueError(
                f"{topic.id} konusu istenen dillerin hiçbirinde tanımlı değil"
            )

        draft_model = _build_draft_model(languages)
        facts = topic.facts[: max(fact_scene_count, 1)]
        scene_count = len(facts) + 2  # intro + fact'ler + outro

        messages: list[dict[str, object]] = [
            {
                "role": "user",
                "content": USER_PROMPT.format(
                    title=" / ".join(
                        f"{lang}: {topic.title[lang]}" for lang in languages
                    ),
                    facts=_format_facts(facts, languages),
                    scene_count=scene_count,
                ),
            }
        ]

        system = SYSTEM_PROMPT.format(
            age_min=age_min,
            age_max=age_max,
            language_rules=_format_language_rules(languages),
        )

        client = self._get_client()
        episode: Episode | None = None
        report: SafetyReport | None = None

        for attempt in range(MAX_REPAIR_ROUNDS + 1):
            response = client.messages.parse(
                model=self.settings.anthropic_model,
                max_tokens=16000,
                system=system,
                thinking={"type": "adaptive"},
                messages=messages,
                output_format=draft_model,
            )

            if response.stop_reason == "refusal":
                raise RuntimeError(
                    "Model bu isteği reddetti; konu metnini gözden geçir. "
                    f"Kategori: {getattr(response.stop_details, 'category', None)}"
                )

            episode = self._to_episode(
                response.parsed_output,
                topic=topic,
                episode_id=episode_id,
                languages=languages,
                age_min=age_min,
                age_max=age_max,
            )
            report = check_episode(episode)
            if report.ok or attempt == MAX_REPAIR_ROUNDS:
                break

            messages.extend(
                [
                    {"role": "assistant", "content": response.parsed_output.model_dump_json()},
                    {
                        "role": "user",
                        "content": REPAIR_PROMPT.format(
                            issues="\n".join(
                                f"- {issue.format()}" for issue in report.errors
                            )
                        ),
                    },
                ]
            )

        assert episode is not None  # döngü en az bir kez çalışır
        return episode

    def _to_episode(
        self,
        draft: BaseModel,
        *,
        topic: Topic,
        episode_id: str,
        languages: list[str],
        age_min: int,
        age_max: int,
    ) -> Episode:
        scenes: list[Scene] = []
        drafted = list(getattr(draft, "scenes", []))
        for index, item in enumerate(drafted, start=1):
            is_hero = item.kind is SceneKind.INTRO and bool(topic.hero_prompt)
            scenes.append(
                Scene(
                    id=f"s{index:02d}",
                    kind=item.kind,
                    visual=Visual(
                        query=item.visual_query,
                        fallback_queries=[topic.title.get("en", topic.id)],
                        motion=item.motion,
                        hero=is_hero,
                        clip_prompt=topic.hero_prompt if is_hero else None,
                    ),
                    narration={
                        lang: getattr(item.narration, lang) for lang in languages
                    },
                )
            )

        return Episode(
            id=episode_id,
            topic_id=topic.id,
            languages=languages,
            age_min=topic.age_min or age_min,
            age_max=topic.age_max or age_max,
            scenes=scenes,
            localizations={
                lang: build_localization(topic, lang, age_min, age_max)
                for lang in languages
            },
            generator=self.name,
        )


def _build_draft_model(languages: list[str]) -> type[BaseModel]:
    """İstenen dillere göre çıktı şemasını dinamik olarak kurar.

    Yapılandırılmış çıktı şemaları dinamik anahtar kabul etmediği için
    (`additionalProperties: false` zorunlu), anlatım alanını her dil için
    ayrı bir alan olarak üretiyoruz.
    """
    narration_model = create_model(
        "Narration",
        __config__=ConfigDict(extra="forbid"),
        **{lang: (str, Field(description=f"{lang} dilindeki anlatım")) for lang in languages},
    )
    scene_model = create_model(
        "SceneDraft",
        __config__=ConfigDict(extra="forbid"),
        kind=(SceneKind, ...),
        visual_query=(str, Field(description="NASA arşivi için İngilizce arama sorgusu")),
        motion=(Motion, ...),
        narration=(narration_model, ...),
    )
    return create_model(
        "EpisodeDraft",
        __config__=ConfigDict(extra="forbid"),
        scenes=(list[scene_model], ...),
    )


def _format_facts(facts, languages: list[str]) -> str:
    lines: list[str] = []
    for index, fact in enumerate(facts, start=1):
        texts = " | ".join(f"{lang}: {fact.text[lang]}" for lang in languages if lang in fact.text)
        lines.append(f"{index}. [görsel: {fact.visual}] {texts}")
    return "\n".join(lines)


def _format_language_rules(languages: list[str]) -> str:
    lines: list[str] = []
    for lang in languages:
        profile = LANGUAGE_PROFILES.get(lang)
        if profile is None:
            lines.append(f"- {lang}: kısa ve sade cümleler kur.")
            continue
        lines.append(
            f"- {lang}: cümle başına en fazla {profile.max_sentence_words} kelime, "
            f"ortalama {profile.max_average_sentence_words:.0f} kelime hedefle. "
            "Her sahne 2-3 cümle olsun."
        )
    return "\n".join(lines)
