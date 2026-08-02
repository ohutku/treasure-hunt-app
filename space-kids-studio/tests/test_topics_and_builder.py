"""Konu kütüphanesi ve şablon senaryo üreteci testleri."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from spacekids.models import SUPPORTED_LANGUAGES, SceneKind
from spacekids.script.builder import TemplateScriptGenerator
from spacekids.script.safety import check_episode
from spacekids.topics import TopicLibrary, load_topics


# --- kütüphane -------------------------------------------------------------


def test_depodaki_kutuphane_yuklenir(topics):
    assert len(topics.topics) >= 5


def test_her_konu_desteklenen_tum_dilleri_kapsar(topics):
    for topic in topics.topics:
        assert set(topic.languages()) == set(SUPPORTED_LANGUAGES), topic.id


def test_her_bilgi_karti_tum_dillerde_metin_tasir(topics):
    for topic in topics.topics:
        for index, fact in enumerate(topic.facts, start=1):
            assert fact.languages() >= set(SUPPORTED_LANGUAGES), f"{topic.id}#{index}"


def test_gorsel_sorgulari_doludur(topics):
    for topic in topics.topics:
        for fact in topic.facts:
            assert len(fact.visual) > 3, topic.id


def test_bilinmeyen_konu_hata_verir(topics):
    with pytest.raises(KeyError, match="bulunamadı"):
        topics.get("yok-boyle-bir-konu")


def test_tekrarlanan_konu_kimligi_reddedilir():
    raw = {
        "topics": [
            {
                "id": "ayni",
                "title": {"tr": "A"},
                "hook": {"tr": "K"},
                "facts": [{"visual": "x y", "tr": "bir"}, {"visual": "z w", "tr": "iki"}],
            },
            {
                "id": "ayni",
                "title": {"tr": "B"},
                "hook": {"tr": "K"},
                "facts": [{"visual": "x y", "tr": "bir"}, {"visual": "z w", "tr": "iki"}],
            },
        ]
    }
    with pytest.raises(ValidationError, match="tekrar eden konu"):
        TopicLibrary.model_validate(raw)


def test_bir_dilde_eksik_bilgi_karti_yakalanir():
    raw = {
        "topics": [
            {
                "id": "eksik",
                "title": {"tr": "A", "en": "A"},
                "hook": {"tr": "K", "en": "H"},
                "facts": [
                    {"visual": "x y", "tr": "bir", "en": "one"},
                    {"visual": "z w", "tr": "iki"},  # en eksik
                ],
            }
        ]
    }
    with pytest.raises(ValidationError, match="eksik"):
        TopicLibrary.model_validate(raw)


def test_konu_secimi_kullanilanlari_atlar(topics):
    used = set(topics.ids()[:-1])
    chosen = topics.pick(exclude=used)
    assert chosen.id == topics.ids()[-1]


def test_tum_konular_kullanildiysa_havuz_sifirlanir(topics):
    chosen = topics.pick(exclude=set(topics.ids()))
    assert chosen.id in topics.ids()


def test_eksik_dosya_anlamli_hata_verir(tmp_path):
    with pytest.raises(FileNotFoundError, match="konu dosyası"):
        load_topics(tmp_path / "yok.yaml")


def test_bozuk_yaml_reddedilir(tmp_path):
    path = tmp_path / "topics.yaml"
    path.write_text(yaml.safe_dump(["liste", "olmaz"]), encoding="utf-8")
    with pytest.raises(ValueError, match="sözlük"):
        load_topics(path)


# --- şablon üreteci --------------------------------------------------------


def test_uretilen_bolum_yapisi(topics):
    topic = topics.get("saturn-rings")
    episode = TemplateScriptGenerator().generate(
        topic,
        episode_id="ep-001",
        languages=["tr", "en"],
        age_min=4,
        age_max=8,
        fact_scene_count=3,
    )
    assert [scene.kind for scene in episode.scenes] == [
        SceneKind.INTRO,
        SceneKind.FACT,
        SceneKind.FACT,
        SceneKind.FACT,
        SceneKind.OUTRO,
    ]
    assert [scene.id for scene in episode.scenes] == ["s01", "s02", "s03", "s04", "s05"]
    assert episode.generator == "template"


def test_uretilen_bolum_bilgi_kartlarini_sirayla_kullanir(topics):
    topic = topics.get("mars")
    episode = TemplateScriptGenerator().generate(
        topic,
        episode_id="ep-001",
        languages=["tr"],
        age_min=4,
        age_max=8,
        fact_scene_count=5,
    )
    fact_scenes = [s for s in episode.scenes if s.kind is SceneKind.FACT]
    assert [scene.text("tr") for scene in fact_scenes] == [
        fact.text["tr"] for fact in topic.facts
    ]


def test_acilis_sahnesi_hero_isaretlenir(topics):
    episode = TemplateScriptGenerator().generate(
        topics.get("rockets"),
        episode_id="ep-001",
        languages=["tr", "en"],
        age_min=4,
        age_max=8,
        fact_scene_count=2,
    )
    heroes = episode.hero_scenes()
    assert len(heroes) == 1
    assert heroes[0].kind is SceneKind.INTRO
    assert heroes[0].visual.clip_prompt


def test_tek_dil_istenirse_sadece_o_dil_uretilir(topics):
    episode = TemplateScriptGenerator().generate(
        topics.get("the-moon"),
        episode_id="ep-001",
        languages=["en"],
        age_min=4,
        age_max=8,
        fact_scene_count=2,
    )
    assert episode.languages == ["en"]
    assert set(episode.scenes[0].narration) == {"en"}


def test_desteklenmeyen_dil_istegi_hata_verir(topics):
    with pytest.raises(ValueError, match="tanımlı değil"):
        TemplateScriptGenerator().generate(
            topics.get("the-moon"),
            episode_id="ep-001",
            languages=["de"],
            age_min=4,
            age_max=8,
            fact_scene_count=2,
        )


def test_tum_konular_denetimden_temiz_gecer(topics):
    """Kütüphanedeki her konu, yayına hazır bir bölüm üretebilmeli."""
    generator = TemplateScriptGenerator()
    failures: list[str] = []
    for topic in topics.topics:
        episode = generator.generate(
            topic,
            episode_id="ep-001",
            languages=["tr", "en"],
            age_min=4,
            age_max=8,
            fact_scene_count=6,
        )
        report = check_episode(episode)
        if not report.ok:
            failures.append(f"{topic.id}: {[i.format() for i in report.errors]}")
    assert not failures, "\n".join(failures)


def test_uretim_deterministiktir(topics):
    """Aynı girdi aynı çıktıyı vermeli — tekrarlanabilirlik için."""
    generator = TemplateScriptGenerator()
    kwargs = dict(
        episode_id="ep-001",
        languages=["tr", "en"],
        age_min=4,
        age_max=8,
        fact_scene_count=4,
    )
    first = generator.generate(topics.get("milky-way"), **kwargs)
    second = generator.generate(topics.get("milky-way"), **kwargs)
    assert first.model_dump(exclude={"created_at"}) == second.model_dump(
        exclude={"created_at"}
    )
