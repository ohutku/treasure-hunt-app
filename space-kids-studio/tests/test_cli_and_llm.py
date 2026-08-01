"""CLI komutları ve Claude tabanlı senaryo üreteci testleri."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from spacekids.cli import app
from spacekids.config import Settings
from spacekids.models import SceneKind
from spacekids.script.llm import LLMScriptGenerator, _build_draft_model, _format_facts
from spacekids.workspace import EpisodeWorkspace

runner = CliRunner()


# =========================== CLI ===========================================


def _run(*args: str) -> object:
    return runner.invoke(app, list(args))


def test_topics_komutu_kutuphaneyi_listeler():
    result = _run("topics")
    assert result.exit_code == 0
    assert "saturn-rings" in result.stdout
    assert "bilgi kartı" in result.stdout


def test_script_komutu_senaryo_uretir(tmp_path):
    result = _run("script", "--topic", "mars", "--episode", "ep-001", "--workspace", str(tmp_path))
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "ep-001" / "episode.json").is_file()
    assert "0 hata" in result.stdout


def test_script_komutu_dil_secimine_uyar(tmp_path):
    result = _run(
        "script", "--topic", "mars", "--episode", "ep-001",
        "--languages", "en", "--workspace", str(tmp_path),
    )
    assert result.exit_code == 0, result.stdout
    episode = EpisodeWorkspace(tmp_path, "ep-001").load_episode()
    assert episode.languages == ["en"]


def test_script_komutu_bilinmeyen_konuda_hata_verir(tmp_path):
    result = _run("script", "--topic", "yok-boyle", "--workspace", str(tmp_path))
    assert result.exit_code != 0


def test_check_komutu_temiz_bolumde_basarili(tmp_path):
    _run("script", "--topic", "the-sun", "--episode", "ep-001", "--workspace", str(tmp_path))
    result = _run("check", "ep-001", "--workspace", str(tmp_path))
    assert result.exit_code == 0
    assert "0 hata" in result.stdout


def test_check_komutu_sorunlu_bolumde_hata_koduyla_biter(tmp_path, episode):
    episode.scenes[1].narration["tr"] = "Bu canavar çocukları çok korkutuyor bence."
    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)

    result = _run("check", episode.id, "--workspace", str(tmp_path))
    assert result.exit_code == 1
    assert "banned_term" in result.stdout


def test_info_komutu_bolum_durumunu_gosterir(tmp_path):
    _run("script", "--topic", "rockets", "--episode", "ep-001", "--workspace", str(tmp_path))
    result = _run("info", "ep-001", "--workspace", str(tmp_path))
    assert result.exit_code == 0
    assert "rockets" in result.stdout
    assert "henüz render edilmedi" in result.stdout


def test_info_komutu_olmayan_bolumde_hata_verir(tmp_path):
    result = _run("info", "ep-yok", "--workspace", str(tmp_path))
    assert result.exit_code == 1
    assert "bulunamadı" in result.stdout


def test_render_komutu_denetimden_kalan_bolumu_reddeder(tmp_path, episode):
    episode.scenes[1].narration["tr"] = "Bu canavar çocukları çok korkutuyor bence."
    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)

    result = _run("render", episode.id, "--offline", "--workspace", str(tmp_path))
    assert result.exit_code == 1
    assert "Güvenlik denetimi" in result.stdout


def test_providers_komutu_saglayicilari_listeler():
    result = _run("providers")
    assert result.exit_code == 0
    assert "byteplus" in result.stdout
    assert "doğrulanmadı" in result.stdout, "doğrulanmamışlık uyarısı görünmeli"


def test_clips_dry_run_aga_cikmadan_istegi_gosterir(tmp_path, episode):
    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)
    result = _run("clips", episode.id, "--dry-run", "--workspace", str(tmp_path))

    assert result.exit_code == 0
    assert "Prova çalıştırması" in result.stdout
    assert "seedance" in result.stdout
    # Süzgecin eklediği kısıtlar istemde görünmeli.
    assert "No recognizable cartoon characters" in result.stdout


def test_clips_dry_run_saglayici_secimine_uyar(tmp_path, episode):
    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)
    result = _run(
        "clips", episode.id, "--dry-run", "--provider", "fal", "--workspace", str(tmp_path)
    )
    assert result.exit_code == 0
    assert "fal" in result.stdout


def test_clips_bilinmeyen_saglayicida_hata_verir(tmp_path, episode):
    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)
    result = _run(
        "clips", episode.id, "--provider", "yok-boyle", "--workspace", str(tmp_path)
    )
    assert result.exit_code == 1
    assert "bulunamadı" in result.stdout


def test_clips_anahtarsiz_calistirma_yol_gosterir(tmp_path, episode, monkeypatch):
    for var in ("SPACEKIDS_CLIP_API_KEY", "SEEDANCE_API_KEY", "BYTEPLUS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)

    result = _run("clips", episode.id, "--workspace", str(tmp_path))
    assert result.exit_code == 1
    assert "SEEDANCE_API_KEY" in result.stdout


def test_clips_hero_sahnesi_yoksa_bilgilendirir(tmp_path, episode):
    for scene in episode.scenes:
        scene.visual.hero = False
        scene.visual.clip_prompt = None
    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)

    result = _run("clips", episode.id, "--workspace", str(tmp_path))
    assert result.exit_code == 0
    assert "hero" in result.stdout


@pytest.mark.slow
def test_render_komutu_video_uretir(tmp_path, episode):
    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)

    result = _run(
        "render", episode.id, "--offline", "--languages", "tr", "--workspace", str(tmp_path)
    )
    assert result.exit_code == 0, result.stdout
    video = tmp_path / episode.id / "out" / "video.tr.mp4"
    assert video.is_file()

    metadata = json.loads(
        (tmp_path / episode.id / "out" / "metadata.tr.json").read_text(encoding="utf-8")
    )
    assert metadata["madeForKids"] is True


# =========================== LLM üreteci ====================================


class _FakeResponse:
    def __init__(self, parsed_output, stop_reason: str = "end_turn") -> None:
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason
        self.stop_details = None


class _FakeMessages:
    """messages.parse çağrılarını kaydeden sahte uç nokta."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.messages = _FakeMessages(responses)


def _draft(languages: list[str], *, tr_fact: str | None = None):
    """Modelin döndüreceği taslağı kurar."""
    model = _build_draft_model(languages)
    texts = {
        "tr": [
            "Merhaba küçük kâşif! Bugün Satürn'ü keşfedeceğiz.",
            tr_fact or "Satürn kocaman bir gezegendir. Etrafında buzdan halkaları vardır.",
            "Bugün çok şey öğrendik. Yine görüşmek üzere sevgili kâşif!",
        ],
        "en": [
            "Hello little explorer! Today we will discover Saturn.",
            "Saturn is a huge planet. It wears rings made of ice.",
            "We learned so much today. See you on our next adventure!",
        ],
    }
    scenes = []
    for index, kind in enumerate([SceneKind.INTRO, SceneKind.FACT, SceneKind.OUTRO]):
        scenes.append(
            {
                "kind": kind,
                "visual_query": f"saturn scene {index}",
                "motion": "zoom_in",
                "narration": {lang: texts[lang][index] for lang in languages},
            }
        )
    return model.model_validate({"scenes": scenes})


def _generator(client, settings: Settings | None = None) -> LLMScriptGenerator:
    return LLMScriptGenerator(settings or Settings(), client=client)


def test_llm_taslagi_bolume_donusturur(topics):
    client = _FakeClient([_FakeResponse(_draft(["tr", "en"]))])
    episode = _generator(client).generate(
        topics.get("saturn-rings"),
        episode_id="ep-001",
        languages=["tr", "en"],
        age_min=4,
        age_max=8,
        fact_scene_count=1,
    )

    assert episode.generator == "claude"
    assert [scene.id for scene in episode.scenes] == ["s01", "s02", "s03"]
    assert episode.scenes[0].kind is SceneKind.INTRO
    assert set(episode.localizations) == {"tr", "en"}
    assert episode.scenes[1].text("en").startswith("Saturn is a huge planet")


def test_llm_acilis_sahnesini_hero_isaretler(topics):
    client = _FakeClient([_FakeResponse(_draft(["tr", "en"]))])
    episode = _generator(client).generate(
        topics.get("saturn-rings"),
        episode_id="ep-001",
        languages=["tr", "en"],
        age_min=4,
        age_max=8,
        fact_scene_count=1,
    )
    heroes = episode.hero_scenes()
    assert len(heroes) == 1
    assert heroes[0].id == "s01"
    assert heroes[0].visual.clip_prompt == topics.get("saturn-rings").hero_prompt


def test_llm_istegi_dogru_model_ve_ayarlarla_gonderilir(topics):
    client = _FakeClient([_FakeResponse(_draft(["tr"]))])
    settings = Settings(anthropic_model="claude-opus-5")
    _generator(client, settings).generate(
        topics.get("mars"),
        episode_id="ep-001",
        languages=["tr"],
        age_min=4,
        age_max=8,
        fact_scene_count=2,
    )

    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["max_tokens"] >= 8000


def test_llm_istemine_bilgi_kartlari_capa_olarak_eklenir(topics):
    client = _FakeClient([_FakeResponse(_draft(["tr"]))])
    topic = topics.get("mars")
    _generator(client).generate(
        topic,
        episode_id="ep-001",
        languages=["tr"],
        age_min=4,
        age_max=8,
        fact_scene_count=2,
    )

    prompt = client.messages.calls[0]["messages"][0]["content"]
    assert topic.facts[0].text["tr"] in prompt
    assert topic.facts[0].visual in prompt


def test_llm_reddi_anlamli_hata_verir(topics):
    client = _FakeClient([_FakeResponse(_draft(["tr"]), stop_reason="refusal")])
    with pytest.raises(RuntimeError, match="reddetti"):
        _generator(client).generate(
            topics.get("mars"),
            episode_id="ep-001",
            languages=["tr"],
            age_min=4,
            age_max=8,
            fact_scene_count=1,
        )


def test_denetimden_kalan_taslak_icin_duzeltme_turu_istenir(topics):
    """İlk taslak yasaklı kelime içeriyorsa modele bulgular geri verilmeli."""
    bad = _draft(["tr"], tr_fact="Bu canavar bütün çocukları çok korkutuyor gerçekten.")
    good = _draft(["tr"])
    client = _FakeClient([_FakeResponse(bad), _FakeResponse(good)])

    episode = _generator(client).generate(
        topics.get("saturn-rings"),
        episode_id="ep-001",
        languages=["tr"],
        age_min=4,
        age_max=8,
        fact_scene_count=1,
    )

    assert len(client.messages.calls) == 2, "düzeltme turu istenmeliydi"
    repair_prompt = client.messages.calls[1]["messages"][-1]["content"]
    assert "banned_term" in repair_prompt
    assert "canavar" not in episode.scenes[1].text("tr")


def test_duzeltme_turu_sinirlidir(topics):
    """Model ısrarla sorunlu üretirse sonsuz döngüye girilmemeli."""
    bad = _draft(["tr"], tr_fact="Bu canavar bütün çocukları çok korkutuyor gerçekten.")
    client = _FakeClient([_FakeResponse(bad)])

    episode = _generator(client).generate(
        topics.get("saturn-rings"),
        episode_id="ep-001",
        languages=["tr"],
        age_min=4,
        age_max=8,
        fact_scene_count=1,
    )
    assert len(client.messages.calls) == 2
    assert episode is not None  # son taslak yine de döner, denetim çağıranda yapılır


def test_desteklenmeyen_dil_llm_uretecinde_de_reddedilir(topics):
    client = _FakeClient([_FakeResponse(_draft(["tr"]))])
    with pytest.raises(ValueError, match="tanımlı değil"):
        _generator(client).generate(
            topics.get("mars"),
            episode_id="ep-001",
            languages=["de"],
            age_min=4,
            age_max=8,
            fact_scene_count=1,
        )


def test_taslak_semasi_istenen_dilleri_icerir():
    schema = _build_draft_model(["tr", "en"]).model_json_schema()
    narration = schema["$defs"]["Narration"]
    assert set(narration["properties"]) == {"tr", "en"}
    assert narration.get("additionalProperties") is False


def test_bilgi_kartlari_isteme_okunakli_bicimlenir(topics):
    formatted = _format_facts(topics.get("mars").facts[:2], ["tr", "en"])
    assert formatted.startswith("1. [görsel:")
    assert "2. [görsel:" in formatted
