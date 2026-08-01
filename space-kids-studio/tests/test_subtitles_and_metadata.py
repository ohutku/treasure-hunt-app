"""Altyazı ve yayın meta verisi testleri."""

from __future__ import annotations

import re

import pytest

from spacekids.media.subtitles import (
    MAX_CUE_CHARS,
    build_srt,
    format_timestamp,
    split_into_cues,
)
from spacekids.models import MAX_TITLE_LENGTH, SceneTiming
from spacekids.publish.metadata import (
    MIN_CHAPTER_SECONDS,
    build_chapters,
    build_localization,
    build_tags,
    build_title,
    build_youtube_metadata,
)
from spacekids.publish.metadata import format_timestamp as chapter_timestamp

_SRT_TIME = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})$")


def _timings(episode, duration: float = 12.0) -> list[SceneTiming]:
    return [
        SceneTiming(scene_id=scene.id, start=index * duration, duration=duration)
        for index, scene in enumerate(episode.scenes)
    ]


# --- altyazı ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00:00,000"),
        (1.5, "00:00:01,500"),
        (61.25, "00:01:01,250"),
        (3661.007, "01:01:01,007"),
        (-5, "00:00:00,000"),
    ],
)
def test_srt_zaman_damgasi(seconds, expected):
    assert format_timestamp(seconds) == expected


def test_cue_bolme_sureyi_tam_dagitir():
    cues = split_into_cues("Bir cümle. İkinci cümle burada.", start=10.0, duration=6.0)
    assert len(cues) == 2
    assert cues[0][0] == pytest.approx(10.0)
    total = sum(duration for _, duration, _ in cues)
    assert total == pytest.approx(6.0)


def test_cue_suresi_kelime_sayisina_gore_paylasilir():
    cues = split_into_cues("Kısa. Bu cümle çok daha fazla kelime içeriyor.", 0.0, 10.0)
    assert cues[1][1] > cues[0][1]


def test_uzun_cumle_karakter_sinirinda_bolunur():
    long_sentence = " ".join(["kelime"] * 40)
    cues = split_into_cues(long_sentence, 0.0, 20.0)
    assert len(cues) > 1
    assert all(len(text) <= MAX_CUE_CHARS for _, _, text in cues)


def test_bos_metin_cue_uretmez():
    assert split_into_cues("   ", 0.0, 5.0) == []


def test_srt_bicimi_gecerli(episode):
    content = build_srt(episode, "tr", _timings(episode))
    blocks = [block for block in content.strip().split("\n\n") if block]
    assert blocks
    for index, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        assert lines[0] == str(index), "sıra numaraları ardışık olmalı"
        assert _SRT_TIME.match(lines[1]), lines[1]
        assert lines[2].strip()


def test_altyazi_kareleri_ust_uste_binmez(episode):
    content = build_srt(episode, "tr", _timings(episode))
    times = _SRT_TIME.findall(content)
    previous_end = -1.0
    for match in times:
        start = int(match[0]) * 3600 + int(match[1]) * 60 + int(match[2]) + int(match[3]) / 1000
        end = int(match[4]) * 3600 + int(match[5]) * 60 + int(match[6]) + int(match[7]) / 1000
        assert start >= previous_end - 1e-6, "kareler çakışıyor"
        assert end >= start
        previous_end = end


def test_altyazi_dile_gore_degisir(episode):
    tr = build_srt(episode, "tr", _timings(episode))
    en = build_srt(episode, "en", _timings(episode))
    assert tr != en
    assert "Merhaba" in tr
    assert "Hello" in en


# --- meta veri -------------------------------------------------------------


def test_baslik_youtube_sinirini_asmaz(topics):
    for topic in topics.topics:
        for language in topic.languages():
            assert len(build_title(topic, language)) <= MAX_TITLE_LENGTH


def test_uzun_konu_basligi_kisaltilir(topics):
    topic = topics.get("mars").model_copy(deep=True)
    topic.title["tr"] = "Ç" * 140
    title = build_title(topic, "tr")
    assert len(title) <= MAX_TITLE_LENGTH


def test_etiketler_konuya_ozel_ve_genel_olanlari_birlestirir(topics):
    tags = build_tags(topics.get("mars"), "tr")
    assert "mars" in tags
    assert "uzay" in tags
    assert len(tags) == len(set(tag.casefold() for tag in tags))


def test_etiket_toplam_uzunlugu_sinir_altinda(topics):
    for topic in topics.topics:
        for language in topic.languages():
            tags = build_tags(topic, language)
            total = sum(len(tag) for tag in tags) + max(len(tags) - 1, 0)
            assert total <= 500


def test_yerellestirme_paketi_dolu(topics):
    localization = build_localization(topics.get("the-sun"), "tr", 4, 8)
    assert localization.title
    assert "4-8" in localization.description
    assert localization.tags
    assert localization.hook == topics.get("the-sun").hook["tr"]


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0:00"), (65, "1:05"), (3725, "1:02:05")],
)
def test_bolum_zaman_damgasi(seconds, expected):
    assert chapter_timestamp(seconds) == expected


def test_bolum_isaretleri_ilk_sifirdan_baslar(episode):
    chapters = build_chapters(episode, "tr", _timings(episode))
    assert chapters
    assert chapters[0][0] == "0:00"


def test_kisa_sahneler_birlestirilir(episode):
    """10 saniyeden kısa sahneler tek bölüm işaretinde toplanmalı."""
    short = [
        SceneTiming(scene_id=scene.id, start=index * 2.0, duration=2.0)
        for index, scene in enumerate(episode.scenes)
    ]
    chapters = build_chapters(episode, "tr", short)
    # 3 sahne x 2 sn = 6 sn: en az 3 işaret kuralı sağlanamaz, boş dönmeli.
    assert chapters == []


def test_yeterince_uzun_sahneler_bolum_isareti_uretir(episode):
    timings = _timings(episode, duration=MIN_CHAPTER_SECONDS + 5)
    chapters = build_chapters(episode, "tr", timings)
    assert len(chapters) >= 3


def test_youtube_metadata_paketi(episode):
    timings = _timings(episode, duration=20.0)
    metadata = build_youtube_metadata(episode, "tr", timings, duration=60.0)
    assert metadata["madeForKids"] is True
    assert metadata["categoryId"] == "27"
    assert metadata["privacyStatus"] == "private"
    assert metadata["defaultLanguage"] == "tr"
    assert metadata["durationSeconds"] == 60.0
    assert "Bölümler:" in metadata["description"]


def test_metadata_aciklamasi_sinirlari_asmaz(episode):
    metadata = build_youtube_metadata(episode, "tr", _timings(episode), duration=36.0)
    assert len(metadata["description"]) <= 5000
    assert len(metadata["title"]) <= MAX_TITLE_LENGTH
