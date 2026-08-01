"""Yayın metinleri: başlık, açıklama, etiketler ve YouTube meta verisi.

İki aşamada kullanılır:

* `build_localization` — senaryo üretilirken, henüz süreler bilinmezken çağrılır.
* `build_youtube_metadata` — render bittikten sonra, gerçek zamanlamalarla
  bölüm işaretleri (chapters) ekleyerek yükleme paketini hazırlar.
"""

from __future__ import annotations

from ..models import (
    MAX_DESCRIPTION_LENGTH,
    MAX_TAGS_TOTAL_LENGTH,
    MAX_TITLE_LENGTH,
    Episode,
    Localization,
    SceneKind,
    SceneTiming,
)
from ..script.safety import split_sentences
from ..topics import Topic

#: YouTube kategori kimliği: 27 = Education.
YOUTUBE_CATEGORY_EDUCATION = "27"

#: YouTube bölüm işaretleri en az 3 tane olmalı ve her biri en az 10 sn sürmeli.
MIN_CHAPTER_SECONDS = 10.0
MIN_CHAPTER_COUNT = 3

TITLE_SUFFIX: dict[str, str] = {
    "tr": "Çocuklar İçin Uzay",
    "en": "Space for Kids",
}

DESCRIPTION_TEMPLATES: dict[str, str] = {
    "tr": (
        "{title} hakkında merak ettiğin her şey! Küçük kâşifler için hazırlanmış "
        "sakin, eğlenceli bir uzay yolculuğu.\n\n"
        "🚀 Bu bölümde:\n{bullets}\n\n"
        "👶 {age_min}-{age_max} yaş arası çocuklar için hazırlanmıştır.\n"
        "🔭 Görseller NASA'nın kamuya açık arşivinden alınmıştır.\n\n"
        "{hashtags}"
    ),
    "en": (
        "Everything you wondered about {title}! A calm, playful space journey "
        "made for little explorers.\n\n"
        "🚀 In this episode:\n{bullets}\n\n"
        "👶 Made for children aged {age_min}-{age_max}.\n"
        "🔭 Imagery comes from NASA's public domain archive.\n\n"
        "{hashtags}"
    ),
}

BASE_TAGS: dict[str, list[str]] = {
    "tr": ["uzay", "çocuklar için", "eğitici video", "bilim", "okul öncesi", "gezegenler"],
    "en": ["space", "for kids", "educational", "science", "preschool", "planets"],
}

HASHTAGS: dict[str, list[str]] = {
    "tr": ["#uzay", "#çocuklariçin", "#eğitici", "#bilim"],
    "en": ["#space", "#forkids", "#educational", "#science"],
}

CHAPTER_INTRO_LABEL: dict[str, str] = {"tr": "Başlangıç", "en": "Intro"}
CHAPTER_OUTRO_LABEL: dict[str, str] = {"tr": "Hoşça kal", "en": "Goodbye"}


def build_title(topic: Topic, language: str) -> str:
    """Konu başlığından YouTube başlığı üretir, sınırı aşarsa sadeleştirir."""
    base = topic.title.get(language) or topic.title[topic.languages()[0]]
    suffix = TITLE_SUFFIX.get(language, TITLE_SUFFIX["en"])
    title = f"{topic.emoji} {base} | {suffix}"
    if len(title) <= MAX_TITLE_LENGTH:
        return title
    title = f"{base} | {suffix}"
    if len(title) <= MAX_TITLE_LENGTH:
        return title
    return base[:MAX_TITLE_LENGTH]


def build_tags(topic: Topic, language: str) -> list[str]:
    """Konuya özel + genel etiketleri birleştirir, YouTube sınırına sığdırır."""
    candidates = [*topic.keywords_for(language), *BASE_TAGS.get(language, [])]
    tags: list[str] = []
    seen: set[str] = set()
    total = 0
    for tag in candidates:
        cleaned = " ".join(tag.split())
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        # Etiketler virgülle ayrıldığı için her ek etiket 1 karakter daha yer kaplar.
        cost = len(cleaned) + (1 if tags else 0)
        if total + cost > MAX_TAGS_TOTAL_LENGTH:
            break
        seen.add(key)
        tags.append(cleaned)
        total += cost
    return tags


def build_description(topic: Topic, language: str, age_min: int, age_max: int) -> str:
    """Bilgi kartlarının ilk cümlelerinden madde işaretli açıklama kurar."""
    bullets = []
    for fact in topic.facts:
        text = fact.text.get(language)
        if not text:
            continue
        sentences = split_sentences(text)
        headline = sentences[0] if sentences else text
        bullets.append(f"• {headline}")

    template = DESCRIPTION_TEMPLATES.get(language, DESCRIPTION_TEMPLATES["en"])
    description = template.format(
        title=topic.title.get(language, topic.id),
        bullets="\n".join(bullets),
        age_min=age_min,
        age_max=age_max,
        hashtags=" ".join(HASHTAGS.get(language, HASHTAGS["en"])),
    )
    if len(description) > MAX_DESCRIPTION_LENGTH:
        description = description[: MAX_DESCRIPTION_LENGTH - 1].rstrip() + "…"
    return description


def build_localization(
    topic: Topic, language: str, age_min: int, age_max: int
) -> Localization:
    """Bir dil için başlık/açıklama/etiket/kapak metni paketi."""
    return Localization(
        title=build_title(topic, language),
        description=build_description(topic, language, age_min, age_max),
        tags=build_tags(topic, language),
        hook=topic.hook.get(language) or topic.hook[topic.languages()[0]],
    )


def format_timestamp(seconds: float) -> str:
    """Saniyeyi YouTube bölüm işareti biçimine çevirir (0:05 / 1:23:45)."""
    seconds = max(int(seconds), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def build_chapters(
    episode: Episode, language: str, timings: list[SceneTiming]
) -> list[tuple[str, str]]:
    """Sahne zamanlamalarından YouTube bölüm işaretleri üretir.

    YouTube en az 3 işaret ister ve her işaretin en az 10 saniye sürmesini
    bekler. Kısa sahneler bu yüzden birleştirilir; koşullar yine sağlanamazsa
    boş liste döner ve açıklamaya bölüm eklenmez.
    """
    if not timings:
        return []

    merged: list[tuple[float, list[str]]] = []
    for timing in timings:
        scene = episode.scene(timing.scene_id)
        label_source = scene.text(language)
        sentences = split_sentences(label_source)
        label = sentences[0] if sentences else label_source
        if scene.kind is SceneKind.INTRO:
            label = CHAPTER_INTRO_LABEL.get(language, CHAPTER_INTRO_LABEL["en"])
        elif scene.kind is SceneKind.OUTRO:
            label = CHAPTER_OUTRO_LABEL.get(language, CHAPTER_OUTRO_LABEL["en"])

        if merged and timing.start - merged[-1][0] < MIN_CHAPTER_SECONDS:
            merged[-1][1].append(label)
        else:
            merged.append((timing.start, [label]))

    total_duration = timings[-1].end
    if merged and total_duration - merged[-1][0] < MIN_CHAPTER_SECONDS and len(merged) > 1:
        # Son parça çok kısaysa bir öncekiyle birleştir.
        _, last_labels = merged.pop()
        merged[-1][1].extend(last_labels)

    if len(merged) < MIN_CHAPTER_COUNT:
        return []

    chapters: list[tuple[str, str]] = []
    for index, (start, labels) in enumerate(merged):
        timestamp = format_timestamp(0 if index == 0 else start)
        label = labels[0]
        if len(label) > 60:
            label = label[:57].rstrip() + "…"
        chapters.append((timestamp, label))
    return chapters


def build_youtube_metadata(
    episode: Episode,
    language: str,
    timings: list[SceneTiming],
    *,
    duration: float,
) -> dict[str, object]:
    """YouTube'a yüklemeye hazır meta veri paketi.

    `madeForKids` bilinçli olarak sabit `True`: içerik küçük çocuklar için
    üretiliyor ve YouTube bunun doğru işaretlenmesini zorunlu tutuyor.
    """
    localization = episode.localizations[language]
    description = localization.description

    chapters = build_chapters(episode, language, timings)
    if chapters:
        lines = "\n".join(f"{timestamp} {label}" for timestamp, label in chapters)
        heading = "⏱️ Bölümler:" if language == "tr" else "⏱️ Chapters:"
        addition = f"\n\n{heading}\n{lines}"
        if len(description) + len(addition) <= MAX_DESCRIPTION_LENGTH:
            description = f"{description}{addition}"

    return {
        "title": localization.title,
        "description": description,
        "tags": localization.tags,
        "categoryId": YOUTUBE_CATEGORY_EDUCATION,
        "defaultLanguage": language,
        "defaultAudioLanguage": language,
        "madeForKids": True,
        "privacyStatus": "private",
        "episodeId": episode.id,
        "topicId": episode.topic_id,
        "durationSeconds": round(duration, 2),
        "chapters": [{"time": time, "label": label} for time, label in chapters],
    }
