"""Senaryo üretimi.

İki üreteç var ve ikisi de aynı sözleşmeyi (`ScriptGenerator`) uygular:

* `TemplateScriptGenerator` — konu kütüphanesindeki bilgi kartlarından
  deterministik olarak senaryo kurar. İnternet ve API anahtarı gerektirmez,
  testlerde ve `--offline` modda kullanılır.
* `LLMScriptGenerator` — bir dil modelinden senaryo ister. Bilgi kartlarını
  "doğruluk çıpası" olarak modele verir, böylece uydurma bilgi riski düşer.

Her iki durumda da çıktı `Episode` modeline oturur ve `safety.check_episode`
denetiminden geçirilir.
"""

from __future__ import annotations

from typing import Protocol

from ..models import Episode, Motion, Scene, SceneKind, Visual
from ..publish.metadata import build_localization
from ..topics import Topic

#: Açılış sahnesi kalıpları.
INTRO_TEMPLATES: dict[str, str] = {
    "tr": (
        "Merhaba küçük kâşif! Bugün {title} hakkında bir yolculuğa çıkıyoruz. "
        "Roketimize binelim ve birlikte keşfedelim."
    ),
    "en": (
        "Hello little explorer! Today we are travelling to discover {title}. "
        "Let's climb into our rocket and find out together."
    ),
}

#: Kapanış sahnesi kalıpları.
OUTRO_TEMPLATES: dict[str, str] = {
    "tr": (
        "Bugün {title} hakkında çok şey öğrendik. Yeni bir uzay yolculuğunda "
        "yine görüşmek üzere. Hoşça kal!"
    ),
    "en": (
        "Today we learned so much about {title}. See you on our next space "
        "adventure. Goodbye!"
    ),
}

#: Açılışta kullanılacak genel görsel sorguları.
INTRO_VISUAL = Visual(
    query="Earth from space blue marble",
    fallback_queries=["planet Earth space", "stars nebula"],
    motion=Motion.ZOOM_OUT,
)

OUTRO_VISUAL = Visual(
    query="starfield night sky stars",
    fallback_queries=["nebula colorful", "galaxy stars"],
    motion=Motion.ZOOM_IN,
)


class ScriptGenerator(Protocol):
    """Bir konudan bölüm senaryosu üreten her şeyin sözleşmesi."""

    name: str

    def generate(
        self,
        topic: Topic,
        *,
        episode_id: str,
        languages: list[str],
        age_min: int,
        age_max: int,
        fact_scene_count: int,
    ) -> Episode: ...


class TemplateScriptGenerator:
    """Bilgi kartlarından deterministik senaryo kurar.

    Aynı girdi için daima aynı çıktıyı verir; bu yüzden testlerde referans
    olarak kullanılabilir ve pahalı bir yeniden üretime gerek kalmadan
    tekrarlanabilir sonuç sağlar.
    """

    name = "template"

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

        scenes: list[Scene] = [
            Scene(
                id="s01",
                kind=SceneKind.INTRO,
                visual=INTRO_VISUAL.model_copy(
                    update={
                        "hero": bool(topic.hero_prompt),
                        "clip_prompt": topic.hero_prompt or None,
                    }
                ),
                narration={
                    lang: INTRO_TEMPLATES.get(lang, INTRO_TEMPLATES["en"]).format(
                        title=topic.title.get(lang, topic.id)
                    )
                    for lang in languages
                },
            )
        ]

        selected_facts = topic.facts[: max(fact_scene_count, 1)]
        for index, fact in enumerate(selected_facts, start=2):
            scenes.append(
                Scene(
                    id=f"s{index:02d}",
                    kind=SceneKind.FACT,
                    visual=Visual(
                        query=fact.visual,
                        fallback_queries=[topic.title.get("en", topic.id)],
                        motion=fact.motion,
                    ),
                    narration={lang: fact.text[lang] for lang in languages},
                )
            )

        scenes.append(
            Scene(
                id=f"s{len(scenes) + 1:02d}",
                kind=SceneKind.OUTRO,
                visual=OUTRO_VISUAL,
                narration={
                    lang: OUTRO_TEMPLATES.get(lang, OUTRO_TEMPLATES["en"]).format(
                        title=topic.title.get(lang, topic.id)
                    )
                    for lang in languages
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
