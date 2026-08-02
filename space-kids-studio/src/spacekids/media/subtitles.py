"""SRT altyazı üretimi.

Altyazılar sahne seslerinin **ölçülen** sürelerinden kurulur, tahminden değil —
böylece ses ve yazı birbirinden kaymaz.

Uzun anlatımlar birden fazla altyazı satırına bölünür: bir sahnenin 20 saniyelik
anlatımını tek karede göstermek okunabilir değil. Bölme cümle sınırlarından
yapılır ve süre kelime sayısına göre paylaştırılır.
"""

from __future__ import annotations

from pathlib import Path

from ..models import Episode, SceneTiming
from ..script.safety import count_words, split_sentences

#: Bir altyazı karesinin görünür kalacağı azami süre (saniye).
MAX_CUE_SECONDS = 6.0
#: Bir altyazı karesindeki azami karakter sayısı.
MAX_CUE_CHARS = 84
#: Bir altyazı karesinin asgari süresi — çok kısa kareler göz yorar.
MIN_CUE_SECONDS = 1.2


def format_timestamp(seconds: float) -> str:
    """Saniyeyi SRT zaman damgasına çevirir (00:01:23,456)."""
    seconds = max(seconds, 0.0)
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def split_into_cues(text: str, start: float, duration: float) -> list[tuple[float, float, str]]:
    """Bir sahne anlatımını (başlangıç, süre, metin) üçlülerine böler.

    Süre, parçaların kelime sayısına orantılı dağıtılır: uzun cümle daha uzun
    kalır. Bu, kelime bazlı hizalamaya göre kaba ama seslendirme hızı sabit
    olduğu için pratikte iyi çalışır.
    """
    sentences = [part for part in (split_sentences(text) or [text.strip()]) if part]

    # Çok uzun cümleleri de böl, aksi halde tek kare taşar.
    chunks: list[str] = []
    for sentence in sentences:
        if len(sentence) <= MAX_CUE_CHARS:
            chunks.append(sentence)
            continue
        words = sentence.split()
        current: list[str] = []
        for word in words:
            candidate = " ".join([*current, word])
            if current and len(candidate) > MAX_CUE_CHARS:
                chunks.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            chunks.append(" ".join(current))

    if not chunks:
        return []

    weights = [max(count_words(chunk), 1) for chunk in chunks]
    total_weight = sum(weights)

    cues: list[tuple[float, float, str]] = []
    cursor = start
    for chunk, weight in zip(chunks, weights):
        share = duration * weight / total_weight
        cues.append((cursor, share, chunk))
        cursor += share
    return cues


def build_srt(episode: Episode, language: str, timings: list[SceneTiming]) -> str:
    """Bölümün SRT içeriğini üretir."""
    entries: list[tuple[float, float, str]] = []
    for timing in timings:
        scene = episode.scene(timing.scene_id)
        entries.extend(
            split_into_cues(scene.text(language), timing.start, timing.duration)
        )

    lines: list[str] = []
    for index, (start, duration, text) in enumerate(entries, start=1):
        end = start + max(duration, MIN_CUE_SECONDS if duration > 0 else 0.0)
        # Bir sonraki karenin başlangıcını aşma.
        if index < len(entries):
            end = min(end, entries[index][0])
        lines.append(str(index))
        lines.append(f"{format_timestamp(start)} --> {format_timestamp(end)}")
        lines.append(text)
        lines.append("")

    return "\n".join(lines)


def write_srt(
    episode: Episode, language: str, timings: list[SceneTiming], destination: Path
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(build_srt(episode, language, timings), encoding="utf-8")
    return destination
