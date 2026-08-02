"""Toplu üretim testleri."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from spacekids.cli import app
from spacekids.pipeline import BatchEntry, BatchResult, Pipeline
from spacekids.providers.clips import NullClipProvider
from spacekids.providers.fake import RecordingImageProvider, RecordingVoiceProvider
from spacekids.workspace import EpisodeWorkspace

runner = CliRunner()


def _pipeline(settings) -> Pipeline:
    return Pipeline(
        settings,
        image_provider=RecordingImageProvider(size=(320, 180)),
        voice_provider=RecordingVoiceProvider(duration=0.5),
        clip_provider=NullClipProvider(),
    )


# --- özet nesnesi ----------------------------------------------------------


def test_ozet_basari_ve_hatalari_ayirir():
    result = BatchResult(
        entries=[
            BatchEntry("ep-001", "mars", "ok", 3.0, ["tr"], 55.0),
            BatchEntry("ep-002", "the-moon", "failed", 1.0, error="patladı"),
        ]
    )
    assert result.summary() == "1 başarılı, 1 başarısız"
    assert [entry.episode_id for entry in result.succeeded] == ["ep-001"]
    assert [entry.episode_id for entry in result.failed] == ["ep-002"]
    assert result.ok is False
    assert result.total_video_seconds == 55.0
    assert result.total_elapsed == 4.0


def test_hatasiz_ozet_ok_doner():
    result = BatchResult(entries=[BatchEntry("ep-001", "mars", "ok", 1.0)])
    assert result.ok is True


# --- konu seçimi -----------------------------------------------------------


def test_konular_kutuphane_sirasinda_secilir(settings, topics):
    """Rastgele değil sıralı: aynı komutun ne üreteceği tahmin edilebilir olmalı."""
    pipeline = _pipeline(settings)
    selected = pipeline.select_batch_topics(3)
    assert [topic.id for topic in selected] == topics.ids()[:3]


def test_kullanilmis_konular_secilmez(settings, topics):
    pipeline = _pipeline(settings)
    pipeline.create_script(topics.get(topics.ids()[0]), "ep-001")

    selected = pipeline.select_batch_topics(3)
    assert topics.ids()[0] not in [topic.id for topic in selected]


def test_acik_konu_listesi_sirasiyla_kullanilir(settings):
    pipeline = _pipeline(settings)
    selected = pipeline.select_batch_topics(99, ["mars", "the-sun"])
    assert [topic.id for topic in selected] == ["mars", "the-sun"]


def test_kutuphaneden_fazla_konu_istenirse_mevcutlar_doner(settings, topics):
    pipeline = _pipeline(settings)
    selected = pipeline.select_batch_topics(len(topics.topics) + 5)
    assert len(selected) == len(topics.topics)


# --- yarım kalanlar --------------------------------------------------------


def test_videosu_olmayan_bolum_yarim_sayilir(settings, episode):
    pipeline = _pipeline(settings)
    EpisodeWorkspace(settings.workspace, episode.id).save_episode(episode)
    assert pipeline.pending_episodes() == [episode.id]


def test_dil_bazinda_yarimlik_kontrol_edilir(settings, episode):
    """Bölüm TR+EN tanımlıysa yalnız TR videosu varken hâlâ yarımdır."""
    pipeline = _pipeline(settings)
    workspace = EpisodeWorkspace(settings.workspace, episode.id)
    workspace.save_episode(episode)
    workspace.prepare(["tr"])
    workspace.video_path("tr").write_bytes(b"sahte-video")

    assert pipeline.pending_episodes() == [episode.id]
    assert pipeline.pending_episodes(["tr"]) == []


def test_bozuk_bolum_dosyasi_taramayi_durdurmaz(settings, episode):
    pipeline = _pipeline(settings)
    EpisodeWorkspace(settings.workspace, episode.id).save_episode(episode)
    bozuk = EpisodeWorkspace(settings.workspace, "ep-bozuk")
    bozuk.root.mkdir(parents=True, exist_ok=True)
    bozuk.episode_file.write_text("{ bu geçerli json değil", encoding="utf-8")

    assert pipeline.pending_episodes() == [episode.id]


def test_bos_calisma_alaninda_yarim_yok(settings):
    assert _pipeline(settings).pending_episodes() == []


# --- toplu çalıştırma ------------------------------------------------------


@pytest.mark.slow
def test_toplu_uretim_birden_fazla_bolum_uretir(settings, topics):
    pipeline = _pipeline(settings)
    result = pipeline.run_batch(count=2, languages=["tr"])

    assert result.ok, [entry.error for entry in result.failed]
    assert len(result.succeeded) == 2
    # Her bölüm farklı bir konu almalı.
    assert len({entry.topic_id for entry in result.entries}) == 2
    for entry in result.entries:
        assert Path(pipeline.workspace(entry.episode_id).video_path("tr")).is_file()
        assert entry.video_seconds > 0
        assert entry.languages == ["tr"]


@pytest.mark.slow
def test_toplu_uretim_ilerlemeyi_bildirir(settings):
    pipeline = _pipeline(settings)
    events: list[tuple[str, str]] = []
    pipeline.run_batch(count=1, languages=["tr"], on_progress=lambda s, l: events.append((s, l)))

    assert events[0][0] == "start"
    assert events[-1][0] == "ok"


def test_konu_tukenmesi_raporlanir(settings, topics):
    """Kütüphanede yeterli konu yoksa sessizce az üretmemeli, söylemeli."""
    pipeline = _pipeline(settings)
    for index, topic in enumerate(topics.topics, start=1):
        pipeline.create_script(topic, f"ep-{index:03d}")

    result = pipeline.run_batch(count=3, languages=["tr"])
    assert result.entries == []
    assert result.topics_exhausted == 3


@pytest.mark.slow
def test_bir_bolum_patlarsa_digerleri_devam_eder(settings, episode, topics):
    """Gece süren bir işte tek bozuk bölüm tüm partiyi durdurmamalı."""
    pipeline = _pipeline(settings)

    # Denetimden kalacak bir bölüm hazırla (klasör adı ile id aynı olmalı).
    episode.scenes[1].narration["tr"] = "Bu canavar çocukları çok korkutuyor gerçekten."
    broken = episode.model_copy(update={"id": "ep-bozuk"})
    EpisodeWorkspace(settings.workspace, "ep-bozuk").save_episode(broken)

    # Sağlam bir bölüm de hazırla.
    good = pipeline.create_script(topics.get("mars"), "ep-saglam")
    assert good.topic_id == "mars"

    result = pipeline.run_batch(retry_pending=True, languages=["tr"])

    assert len(result.entries) == 2
    assert len(result.failed) == 1
    assert len(result.succeeded) == 1
    failed = result.failed[0]
    assert failed.episode_id == "ep-bozuk"
    assert "Güvenlik denetimi" in (failed.error or "")
    assert Path(pipeline.workspace("ep-saglam").video_path("tr")).is_file()


@pytest.mark.slow
def test_retry_yalnizca_yarim_kalanlari_isler(settings, topics):
    pipeline = _pipeline(settings)
    pipeline.run_batch(count=1, languages=["tr"])
    assert pipeline.pending_episodes(["tr"]) == []

    # Yarım bir bölüm ekle.
    pipeline.create_script(topics.get("milky-way"), "ep-yarim")
    assert pipeline.pending_episodes(["tr"]) == ["ep-yarim"]

    result = pipeline.run_batch(retry_pending=True, languages=["tr"])
    assert [entry.episode_id for entry in result.entries] == ["ep-yarim"]
    assert result.ok


def test_yarim_kalan_yoksa_retry_bos_doner(settings):
    result = _pipeline(settings).run_batch(retry_pending=True, languages=["tr"])
    assert result.entries == []


# --- CLI -------------------------------------------------------------------


@pytest.mark.slow
def test_batch_komutu_ozet_basar(tmp_path, monkeypatch):
    # CLI kendi ayarlarını kurar; testin dakikalarca render etmemesi için
    # küçük çözünürlük veren bir yapılandırma dosyası gösteriyoruz.
    config = tmp_path / "settings.toml"
    config.write_text(
        "[video]\nwidth = 320\nheight = 180\nfps = 10\n", encoding="utf-8"
    )
    monkeypatch.setenv("SPACEKIDS_CONFIG", str(config))

    result = runner.invoke(
        app,
        ["batch", "--count", "1", "--languages", "tr", "--offline", "--workspace", str(tmp_path)],
    )
    assert result.exit_code == 0, result.stdout
    assert "1 başarılı" in result.stdout
    assert "toplam" in result.stdout


def test_batch_retry_bos_calisma_alaninda_bilgilendirir(tmp_path):
    result = runner.invoke(
        app, ["batch", "--retry", "--workspace", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "Yarım kalan bölüm yok" in result.stdout


def test_batch_konu_tukenince_uyarir(tmp_path, topics):
    """Tüm konular kullanılmışsa kullanıcı ne yapacağını bilmeli."""
    from spacekids.config import Settings

    pipeline = Pipeline(Settings.load(workspace=tmp_path, offline=True))
    for index, topic in enumerate(topics.topics, start=1):
        pipeline.create_script(topic, f"ep-{index:03d}")

    result = runner.invoke(
        app, ["batch", "--count", "2", "--offline", "--workspace", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "yeni konu ekle" in result.stdout


@pytest.mark.slow
def test_batch_hata_durumunda_cikis_kodu_verir(tmp_path, episode):
    """Cron/CI'nin başarısızlığı fark edebilmesi için çıkış kodu 1 olmalı."""
    episode.scenes[1].narration["tr"] = "Bu canavar çocukları çok korkutuyor gerçekten."
    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)

    result = runner.invoke(
        app, ["batch", "--retry", "--languages", "tr", "--offline", "--workspace", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "HATA" in result.stdout
    assert "--retry" in result.stdout


def test_klasor_adi_ile_id_uyusmazligi_yakalanir(settings, episode):
    """Kopyalanmış bir bölüm, kaynağının çıktısını sessizce ezmemeli."""
    EpisodeWorkspace(settings.workspace, "ep-kopya").save_episode(episode)

    with pytest.raises(ValueError, match="uyuşmuyor"):
        EpisodeWorkspace(settings.workspace, "ep-kopya").load_episode()


def test_uyusmazlik_yarim_taramasini_durdurmaz(settings, episode):
    """Bozuk bir klasör, diğer bölümlerin taranmasını engellememeli."""
    pipeline = _pipeline(settings)
    EpisodeWorkspace(settings.workspace, "ep-kopya").save_episode(episode)
    EpisodeWorkspace(settings.workspace, episode.id).save_episode(episode)

    assert pipeline.pending_episodes() == [episode.id]
