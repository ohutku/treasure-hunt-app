"""Çalışma alanı, ffmpeg ve uçtan uca pipeline testleri."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from spacekids.media.ffmpeg import probe_duration, render_silence
from spacekids.media.video import concat_scenes, render_scene, scene_duration
from spacekids.models import Motion
from spacekids.pipeline import Pipeline, PipelineError, build_providers
from spacekids.providers.fake import RecordingImageProvider, RecordingVoiceProvider
from spacekids.providers.images import ProceduralImageProvider
from spacekids.providers.clips import NullClipProvider
from spacekids.publish.thumbnail import THUMBNAIL_SIZE, build_thumbnail
from spacekids.workspace import EpisodeWorkspace, next_episode_id


def _pipeline(settings) -> Pipeline:
    """Sahte sağlayıcılarla, ağa çıkmayan hızlı bir pipeline."""
    return Pipeline(
        settings,
        image_provider=RecordingImageProvider(size=(320, 180)),
        voice_provider=RecordingVoiceProvider(duration=0.8),
        clip_provider=NullClipProvider(),
    )


# --- çalışma alanı ---------------------------------------------------------


def test_bolum_kimligi_artarak_uretilir(tmp_path):
    assert next_episode_id(tmp_path) == "ep-001"
    (tmp_path / "ep-001").mkdir()
    (tmp_path / "ep-007").mkdir()
    (tmp_path / "gecersiz").mkdir()
    assert next_episode_id(tmp_path) == "ep-008"


def test_olmayan_kokte_ilk_kimlik_uretilir(tmp_path):
    assert next_episode_id(tmp_path / "yok") == "ep-001"


def test_calisma_alani_yollari(tmp_path):
    workspace = EpisodeWorkspace(tmp_path, "ep-001")
    assert workspace.image_path("s01").name == "s01.jpg"
    assert workspace.audio_path("s01", "tr").parent.name == "tr"
    assert workspace.video_path("en").name == "video.en.mp4"
    assert workspace.subtitle_path("tr").name == "subtitles.tr.srt"


def test_bolum_kaydedilip_yuklenir(tmp_path, episode):
    workspace = EpisodeWorkspace(tmp_path, episode.id)
    workspace.save_episode(episode)
    assert workspace.exists()

    loaded = workspace.load_episode()
    assert loaded.id == episode.id
    assert [s.id for s in loaded.scenes] == [s.id for s in episode.scenes]


def test_olmayan_bolum_anlamli_hata_verir(tmp_path):
    with pytest.raises(FileNotFoundError, match="senaryo bulunamadı"):
        EpisodeWorkspace(tmp_path, "ep-999").load_episode()


# --- ffmpeg ----------------------------------------------------------------


def test_sessizlik_uretimi_ve_sure_olcumu(tmp_path):
    path = render_silence(tmp_path / "s.mp3", 2.0)
    assert path.is_file()
    assert probe_duration(path) == pytest.approx(2.0, abs=0.15)


def test_olmayan_dosyanin_suresi_olculemez(tmp_path):
    with pytest.raises(FileNotFoundError):
        probe_duration(tmp_path / "yok.mp3")


def test_sahne_suresi_asgari_degerin_altina_dusmez(tmp_path, settings):
    audio = render_silence(tmp_path / "kisa.mp3", 0.3)
    duration = scene_duration(audio, settings.video)
    assert duration == pytest.approx(settings.video.min_scene_duration)


def test_sahne_suresi_ses_uzunlugunu_izler(tmp_path, settings):
    audio = render_silence(tmp_path / "uzun.mp3", 6.0)
    duration = scene_duration(audio, settings.video)
    assert duration == pytest.approx(6.0 + settings.video.scene_padding, abs=0.2)


# --- video -----------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("motion", list(Motion))
def test_her_hareket_turu_render_edilir(tmp_path, settings, motion):
    from .conftest import make_scene

    image = ProceduralImageProvider(size=(320, 180)).fetch(
        make_scene(motion=motion), tmp_path / "img.jpg"
    )
    audio = render_silence(tmp_path / "a.mp3", 1.0)
    output = render_scene(
        destination=tmp_path / "s.mp4",
        audio_path=audio,
        duration=1.5,
        settings=settings.video,
        image_path=image,
        motion=motion,
    )
    assert probe_duration(output) == pytest.approx(1.5, abs=0.2)


@pytest.mark.slow
def test_sahneler_birlestirilince_sureler_toplanir(tmp_path, settings):
    from .conftest import make_scene

    image = ProceduralImageProvider(size=(320, 180)).fetch(
        make_scene(), tmp_path / "img.jpg"
    )
    audio = render_silence(tmp_path / "a.mp3", 1.0)
    paths = [
        render_scene(
            destination=tmp_path / f"s{index}.mp4",
            audio_path=audio,
            duration=1.5,
            settings=settings.video,
            image_path=image,
        )
        for index in range(3)
    ]
    final = concat_scenes(paths, tmp_path / "final.mp4")
    assert probe_duration(final) == pytest.approx(4.5, abs=0.4)


def test_gorselsiz_ve_klipsiz_sahne_reddedilir(tmp_path, settings):
    audio = render_silence(tmp_path / "a.mp3", 1.0)
    with pytest.raises(ValueError, match="görsel ya da klip"):
        render_scene(
            destination=tmp_path / "s.mp4",
            audio_path=audio,
            duration=1.0,
            settings=settings.video,
        )


def test_bos_sahne_listesi_birlestirilemez(tmp_path):
    with pytest.raises(ValueError, match="birleştirilecek sahne yok"):
        concat_scenes([], tmp_path / "final.mp4")


# --- kapak görseli ---------------------------------------------------------


def test_kapak_gorseli_youtube_boyutunda(tmp_path):
    source = tmp_path / "kaynak.jpg"
    Image.new("RGB", (1600, 900), "navy").save(source)
    output = build_thumbnail(source, "Uzaydaki Dev Hulahop!", tmp_path / "kapak.jpg")
    with Image.open(output) as image:
        assert image.size == THUMBNAIL_SIZE


def test_kapak_gorseli_uzun_kancayi_da_sigdirir(tmp_path):
    source = tmp_path / "kaynak.jpg"
    Image.new("RGB", (800, 800), "black").save(source)
    output = build_thumbnail(
        source, "Bu gerçekten çok ama çok uzun bir kanca metni örneği", tmp_path / "k.jpg"
    )
    with Image.open(output) as image:
        assert image.size == THUMBNAIL_SIZE


# --- pipeline --------------------------------------------------------------


def test_saglayicilar_offline_modda_yerel_secilir(settings):
    images, voices, clips = build_providers(settings)
    assert images.name == "procedural"
    assert voices.name == "silent"
    assert clips.name == "none"


def test_saglayicilar_cevrimici_modda_yedekli_kurulur(settings):
    settings.offline = False
    images, voices, clips = build_providers(settings)
    assert "nasa" in images.name and "procedural" in images.name
    assert "edge-tts" in voices.name and "silent" in voices.name
    assert clips.name == "none", "anahtar yokken Seedance kapalı olmalı"


def test_senaryo_uretilir_ve_diske_yazilir(settings, topics):
    pipeline = _pipeline(settings)
    episode = pipeline.create_script(topics.get("saturn-rings"), "ep-001")
    assert pipeline.workspace("ep-001").episode_file.is_file()
    assert episode.topic_id == "saturn-rings"


def test_var_olan_senaryo_yeniden_uretilmez(settings, topics):
    pipeline = _pipeline(settings)
    first = pipeline.create_script(topics.get("mars"), "ep-001")
    # Farklı bir konu istense bile diskteki bölüm korunmalı.
    second = pipeline.create_script(topics.get("the-moon"), "ep-001")
    assert second.topic_id == first.topic_id == "mars"


def test_overwrite_bayragi_senaryoyu_yeniler(settings, topics):
    pipeline = _pipeline(settings)
    pipeline.create_script(topics.get("mars"), "ep-001")
    replaced = pipeline.create_script(topics.get("the-moon"), "ep-001", overwrite=True)
    assert replaced.topic_id == "the-moon"


def test_kullanilmis_konular_tekrar_secilmez(settings, topics):
    pipeline = _pipeline(settings)
    pipeline.create_script(topics.get("mars"), "ep-001")
    used = pipeline._used_topic_ids()
    assert used == {"mars"}
    assert pipeline.resolve_topic(None).id != "mars"


def test_denetimden_kalan_bolum_render_edilmez(settings, episode):
    episode.scenes[1].narration["tr"] = "Bu canavar çocukları korkutuyor gerçekten."
    pipeline = _pipeline(settings)
    with pytest.raises(PipelineError, match="Güvenlik denetimi"):
        pipeline.render(episode, strict=True)


def test_bolumde_olmayan_dil_reddedilir(settings, episode):
    pipeline = _pipeline(settings)
    with pytest.raises(PipelineError, match="bulunmayan dil"):
        pipeline.render(episode, languages=["de"])


@pytest.mark.slow
def test_uctan_uca_render(settings, episode):
    """Tek dilde tam render: görsel, ses, video, altyazı, kapak, metadata."""
    pipeline = _pipeline(settings)
    pipeline.workspace(episode.id).save_episode(episode)

    result = pipeline.render(episode, languages=["tr"])

    manifest = result.manifests["tr"]
    assert Path(manifest.video_path).is_file()
    assert Path(manifest.subtitle_path).is_file()
    assert Path(manifest.thumbnail_path).is_file()
    assert Path(manifest.metadata_path).is_file()

    # Bildirilen süre gerçek video süresiyle uyuşmalı.
    assert probe_duration(Path(manifest.video_path)) == pytest.approx(
        manifest.duration, abs=0.5
    )

    # Zamanlamalar boşluksuz ve sıralı olmalı.
    assert [t.scene_id for t in manifest.timings] == [s.id for s in episode.scenes]
    for previous, current in zip(manifest.timings, manifest.timings[1:]):
        assert current.start == pytest.approx(previous.end)

    metadata = json.loads(Path(manifest.metadata_path).read_text(encoding="utf-8"))
    assert metadata["madeForKids"] is True


@pytest.mark.slow
def test_iki_dil_ayri_video_uretir(settings, episode):
    pipeline = _pipeline(settings)
    pipeline.workspace(episode.id).save_episode(episode)

    result = pipeline.render(episode, languages=["tr", "en"])
    assert set(result.manifests) == {"tr", "en"}

    tr_video = Path(result.manifests["tr"].video_path)
    en_video = Path(result.manifests["en"].video_path)
    assert tr_video != en_video
    assert tr_video.is_file() and en_video.is_file()

    # Görseller diller arasında paylaşılır: sahne başına tek görsel çağrısı.
    assert pipeline.image_provider.calls == [scene.id for scene in episode.scenes]


@pytest.mark.slow
def test_render_kaldigi_yerden_devam_eder(settings, episode):
    """İkinci çalıştırma pahalı adımları tekrarlamamalı."""
    pipeline = _pipeline(settings)
    pipeline.workspace(episode.id).save_episode(episode)

    pipeline.render(episode, languages=["tr"])
    workspace = pipeline.workspace(episode.id)
    fingerprints = {
        path: path.stat().st_mtime_ns
        for path in (
            workspace.audio_path("s01", "tr"),
            workspace.image_path("s01"),
            workspace.scene_video_path("s01", "tr"),
        )
    }
    assert all(path.is_file() for path in fingerprints)

    pipeline.render(episode, languages=["tr"])

    # Pahalı ara ürünler dokunulmadan kalmalı — asıl devam edebilirlik garantisi bu.
    for path, mtime in fingerprints.items():
        assert path.stat().st_mtime_ns == mtime, f"{path.name} gereksiz yere yeniden üretildi"


@pytest.mark.slow
def test_run_uctan_uca_calisir(settings):
    pipeline = _pipeline(settings)
    result = pipeline.run(topic_id="the-moon", episode_id="ep-100", languages=["tr"])
    assert result.episode.topic_id == "the-moon"
    assert Path(result.manifests["tr"].video_path).is_file()
