"""Yaşa uygunluk denetimi testleri."""

from __future__ import annotations

import pytest

from spacekids.script.safety import (
    Severity,
    check_episode,
    count_words,
    estimate_duration,
    split_sentences,
    _find_terms,
)

from .conftest import make_localization, make_scene
from spacekids.models import Episode


def _episode_with(tr: str, en: str = "Saturn is a huge planet with bright rings.") -> Episode:
    """Tek sahnesi verilen metni taşıyan bir bölüm kurar."""
    return Episode(
        id="ep-safety",
        topic_id="t",
        languages=["tr", "en"],
        age_min=4,
        age_max=8,
        scenes=[
            make_scene("s01", tr=tr, en=en),
            make_scene("s02"),
        ],
        localizations={"tr": make_localization("tr"), "en": make_localization("en")},
    )


def _codes(report, severity: Severity | None = None) -> set[str]:
    issues = report.issues if severity is None else [
        issue for issue in report.issues if issue.severity is severity
    ]
    return {issue.code for issue in issues}


# --- kelime sınırı: alt-dize eşleşmesinin yol açtığı yanlış alarmlar --------


@pytest.mark.parametrize(
    "text",
    [
        "Roketin alt bölümü ayrılır ve yoluna devam eder.",  # "bölüm" içinde "ölüm"
        "Kocaman kanatları Güneş'ten enerji toplar.",  # "kanatları" içinde "kan"
        "Bu bölümde gezegenleri tanıyacağız.",
    ],
)
def test_turkce_alt_dize_yanlis_alarm_vermez(text):
    report = check_episode(_episode_with(text))
    assert "banned_term" not in _codes(report), report.summary()


@pytest.mark.parametrize(
    "text",
    [
        "The Sun gives us light and warmth every day.",  # "warmth" içinde "war"
        "We are going towards the bright star slowly.",  # "towards" içinde "war"
    ],
)
def test_ingilizce_alt_dize_yanlis_alarm_vermez(text):
    report = check_episode(_episode_with("Satürn çok büyük bir gezegendir.", text))
    assert "banned_term" not in _codes(report), report.summary()


def test_gercek_yasakli_kelime_yakalanir():
    report = check_episode(_episode_with("Bu canavar çocukları korkutuyor."))
    assert "banned_term" in _codes(report, Severity.ERROR)
    assert not report.ok


def test_yasakli_kelime_ekleriyle_de_yakalanir():
    # "öldür*" kalıbı ekli hâlleri de yakalamalı.
    report = check_episode(_episode_with("Yıldızlar gezegenleri öldürüyor."))
    assert "banned_term" in _codes(report, Severity.ERROR)


def test_dikkat_kelimesi_uyari_uretir_hata_degil():
    report = check_episode(
        _episode_with("Bir yıldızın patlaması gökyüzünde çok parlak bir ışık oluşturur.")
    )
    assert "caution_term" in _codes(report, Severity.WARNING)
    assert report.ok, "dikkat kelimeleri render'ı durdurmamalı"


def test_ticari_yonlendirme_hata_uretir():
    report = check_episode(_episode_with("Hemen alın, bu oyuncağı satın alın çocuklar."))
    assert "commercial_language" in _codes(report, Severity.ERROR)


def test_baglanti_iceren_anlatim_reddedilir():
    report = check_episode(_episode_with("Daha fazlası için www.ornek.com adresine bak."))
    assert "url_in_narration" in _codes(report, Severity.ERROR)


def test_cok_kisa_sahne_hata_uretir():
    report = check_episode(_episode_with("Satürn büyüktür."))
    assert "scene_too_short" in _codes(report, Severity.ERROR)


def test_cok_uzun_cumle_uyari_uretir():
    long_sentence = " ".join(["kelime"] * 25) + "."
    report = check_episode(_episode_with(f"Satürn çok büyük bir gezegendir. {long_sentence}"))
    assert "sentence_too_long" in _codes(report, Severity.WARNING)


def test_temiz_bolum_denetimden_gecer(episode):
    report = check_episode(episode)
    assert report.ok
    assert not report.errors


def test_rapor_dil_basina_sure_tahmini_uretir(episode):
    report = check_episode(episode)
    assert set(report.estimated_durations) == {"tr", "en"}
    assert all(value > 0 for value in report.estimated_durations.values())


def test_bos_etiket_listesi_uyari_uretir(episode):
    episode.localizations["tr"].tags = []
    report = check_episode(episode)
    assert "missing_tags" in _codes(report, Severity.WARNING)


def test_tamami_buyuk_harf_baslik_uyari_uretir(episode):
    episode.localizations["tr"].title = "SATURN HAKKINDA HERSEY"
    report = check_episode(episode)
    assert "shouting_title" in _codes(report, Severity.WARNING)


# --- yardımcılar -----------------------------------------------------------


def test_cumle_ayirma():
    assert split_sentences("Bir. İki! Üç? ") == ["Bir", "İki", "Üç"]


def test_kesme_isareti_kelimeyi_bolmez():
    # "Satürn'ü" tek kelime sayılmalı, iki değil.
    assert count_words("Satürn'ü gördük") == 2
    assert count_words("let's go now") == 3


def test_sure_tahmini_kelime_sayisiyla_artar():
    short = estimate_duration("bir iki üç", "tr")
    long = estimate_duration("bir iki üç dört beş altı", "tr")
    assert long > short > 0


def test_terim_esleme_yildizli_ve_yildizsiz():
    assert _find_terms("kanatları açtı", ("kan",)) == []
    assert _find_terms("kan aktı", ("kan",)) == ["kan"]
    assert _find_terms("öldürdü", ("öldür*",)) == ["öldürdü"]
