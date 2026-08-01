"""Veri modeli doğrulamaları."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from spacekids.models import (
    MAX_TAGS_TOTAL_LENGTH,
    Episode,
    Localization,
    Motion,
    Scene,
    SceneTiming,
    Visual,
)

from .conftest import make_localization, make_scene


def test_scene_id_bicimi_dogrulanir():
    with pytest.raises(ValidationError, match="s01"):
        Scene(id="sahne1", visual=Visual(query="x y"), narration={"tr": "merhaba"})


def test_bos_anlatim_reddedilir():
    with pytest.raises(ValidationError, match="boş olamaz"):
        Scene(id="s01", visual=Visual(query="x y"), narration={"tr": "   "})


def test_anlatimdaki_fazla_bosluklar_temizlenir():
    scene = Scene(
        id="s01",
        visual=Visual(query="x y"),
        narration={"tr": "  Satürn   çok\n büyüktür.  "},
    )
    assert scene.text("tr") == "Satürn çok büyüktür."


def test_eksik_dilde_metin_istemek_hata_verir(episode):
    with pytest.raises(KeyError, match="de"):
        episode.scenes[0].text("de")


def test_visual_sorgu_sirasi_korunur():
    visual = Visual(query="birincil", fallback_queries=["yedek1", "yedek2"])
    assert visual.queries == ["birincil", "yedek1", "yedek2"]


def test_etiketler_tekillestirilir_ve_sira_korunur():
    localization = Localization(
        title="Başlık",
        description="Açıklama",
        tags=["Uzay", "uzay", "  çocuk  ", "UZAY", "çocuk"],
        hook="Kanca",
    )
    assert localization.tags == ["Uzay", "çocuk"]


def test_etiket_uzunluk_siniri_uygulanir():
    with pytest.raises(ValidationError, match="toplam uzunluğu"):
        Localization(
            title="Başlık",
            description="Açıklama",
            tags=[f"etiket-{index:04d}" * 3 for index in range(30)],
            hook="Kanca",
        )


def test_etiket_siniri_tam_sinirda_kabul_edilir():
    # Tam olarak sınıra oturan bir etiket listesi geçmeli.
    tag = "a" * MAX_TAGS_TOTAL_LENGTH
    localization = Localization(
        title="Başlık", description="Açıklama", tags=[tag], hook="Kanca"
    )
    assert localization.tags == [tag]


def test_eksik_yerellestirme_yakalanir():
    with pytest.raises(ValidationError, match="yerelleştirme"):
        Episode(
            id="ep-1",
            topic_id="t",
            languages=["tr", "en"],
            age_min=4,
            age_max=8,
            scenes=[make_scene("s01"), make_scene("s02")],
            localizations={"tr": make_localization("tr")},
        )


def test_eksik_dildeki_sahne_anlatimi_yakalanir():
    scene = Scene(id="s02", visual=Visual(query="x y"), narration={"tr": "sadece türkçe"})
    with pytest.raises(ValidationError, match="anlatımı olmayan"):
        Episode(
            id="ep-1",
            topic_id="t",
            languages=["tr", "en"],
            age_min=4,
            age_max=8,
            scenes=[make_scene("s01"), scene],
            localizations={
                "tr": make_localization("tr"),
                "en": make_localization("en"),
            },
        )


def test_tekrarlanan_sahne_kimlikleri_reddedilir():
    with pytest.raises(ValidationError, match="benzersiz"):
        Episode(
            id="ep-1",
            topic_id="t",
            languages=["tr"],
            age_min=4,
            age_max=8,
            scenes=[make_scene("s01"), make_scene("s01")],
            localizations={"tr": make_localization("tr")},
        )


def test_yas_araligi_tutarliligi():
    with pytest.raises(ValidationError, match="age_max"):
        Episode(
            id="ep-1",
            topic_id="t",
            languages=["tr"],
            age_min=8,
            age_max=4,
            scenes=[make_scene("s01"), make_scene("s02")],
            localizations={"tr": make_localization("tr")},
        )


def test_hero_sahneleri_filtrelenir(episode):
    assert [scene.id for scene in episode.hero_scenes()] == ["s01"]


def test_bolum_json_gidis_donusu(episode):
    restored = Episode.model_validate_json(episode.model_dump_json())
    assert restored.id == episode.id
    assert [s.id for s in restored.scenes] == [s.id for s in episode.scenes]
    assert restored.scenes[2].visual.motion is Motion.PAN_RIGHT
    assert restored.localizations["tr"].hook == episode.localizations["tr"].hook


def test_sahne_zamanlamasi_bitis_hesabi():
    timing = SceneTiming(scene_id="s01", start=2.5, duration=4.0)
    assert timing.end == pytest.approx(6.5)


def test_sifir_sureli_zamanlama_reddedilir():
    with pytest.raises(ValidationError):
        SceneTiming(scene_id="s01", start=0.0, duration=0.0)
