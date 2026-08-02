"""NASA arşiv videosu testleri — hiçbiri gerçek ağa çıkmaz."""

from __future__ import annotations

import httpx
import pytest

from spacekids.config import Settings
from spacekids.models import Motion, Scene, SceneKind, Visual
from spacekids.pipeline import build_clip_provider
from spacekids.providers.clips import NullClipProvider
from spacekids.providers.nasa_video import (
    DEFAULT_MAX_BYTES,
    NasaVideoProvider,
    VideoCandidate,
)

SEARCH_PATH = "/search"
COLLECTION_URL = "https://images-assets.nasa.gov/video/x/collection.json"


def _scene(scene_id: str = "s01", queries: list[str] | None = None) -> Scene:
    queries = queries or ["Saturn rings Cassini", "Saturn"]
    return Scene(
        id=scene_id,
        kind=SceneKind.FACT,
        visual=Visual(
            query=queries[0],
            fallback_queries=queries[1:],
            motion=Motion.ZOOM_IN,
        ),
        narration={"tr": "Satürn kocaman bir gezegendir ve halkaları vardır."},
    )


def _search_payload(count: int = 1) -> dict:
    return {
        "collection": {
            "items": [
                {
                    "href": COLLECTION_URL,
                    "data": [
                        {
                            "nasa_id": "NASA-1",
                            "title": "Cassini Satürn'e Yaklaşıyor",
                            "description": "  Cassini uzay aracının   Satürn halkalarına yaklaşışı.  ",
                        }
                    ],
                }
                for _ in range(count)
            ]
        }
    }


def _provider(handler, **kwargs) -> NasaVideoProvider:
    return NasaVideoProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs
    )


def _standard_handler(
    files: list[str] | None = None,
    *,
    content: bytes = b"MP4-VERISI",
    content_length: str | None = None,
    search_payload: dict | None = None,
):
    files = files if files is not None else ["https://x/video~large.mp4"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == SEARCH_PATH:
            return httpx.Response(200, json=search_payload or _search_payload())
        if request.url.path.endswith("collection.json"):
            return httpx.Response(200, json=files)
        if request.method == "HEAD":
            headers = {"content-length": content_length} if content_length else {}
            return httpx.Response(200, headers=headers)
        return httpx.Response(200, content=content)

    return handler


# --- arama ve varlık seçimi -------------------------------------------------


def test_aday_bulunur_ve_bilgileri_okunur():
    provider = _provider(_standard_handler(content_length="1048576"))
    candidate = provider.preview(_scene())

    assert candidate is not None
    assert candidate.title == "Cassini Satürn'e Yaklaşıyor"
    assert candidate.nasa_id == "NASA-1"
    # Açıklamadaki fazla boşluklar temizlenmeli.
    assert "  " not in candidate.description
    assert candidate.size_bytes == 1048576
    assert candidate.size_label == "1.0 MB"


def test_varlik_tercih_sirasi_uygulanir():
    """`~orig` yayın masteri olabilir; 1080p için `~large` tercih edilmeli."""
    files = [
        "https://x/video~orig.mp4",
        "https://x/video~small.mp4",
        "https://x/video~large.mp4",
        "https://x/video~medium.mp4",
    ]
    candidate = _provider(_standard_handler(files)).preview(_scene())
    assert candidate is not None
    assert candidate.url.endswith("~large.mp4")


def test_altyazi_ve_ses_dosyalari_yok_sayilir():
    files = [
        "https://x/video.srt",
        "https://x/video.vtt",
        "https://x/thumb.jpg",
        "https://x/video~medium.mp4",
    ]
    candidate = _provider(_standard_handler(files)).preview(_scene())
    assert candidate is not None
    assert candidate.url.endswith(".mp4")


def test_bilinmeyen_sonek_de_kabul_edilir():
    """Tercih listesindeki hiçbir sonek yoksa eldeki mp4 kullanılır."""
    candidate = _provider(_standard_handler(["https://x/beklenmedik-ad.mp4"])).preview(_scene())
    assert candidate is not None
    assert candidate.url.endswith("beklenmedik-ad.mp4")


def test_mp4_yoksa_aday_yok():
    assert _provider(_standard_handler(["https://x/video.srt"])).preview(_scene()) is None


def test_sonuc_yoksa_yedek_sorgu_denenir():
    """İlk sorgu boşsa ikinci sorguya geçilmeli."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == SEARCH_PATH:
            query = request.url.params.get("q", "")
            seen.append(query)
            if query == "Saturn rings Cassini":
                return httpx.Response(200, json={"collection": {"items": []}})
            return httpx.Response(200, json=_search_payload())
        if request.url.path.endswith("collection.json"):
            return httpx.Response(200, json=["https://x/v~large.mp4"])
        return httpx.Response(200, content=b"x")

    candidate = _provider(handler).preview(_scene())
    assert candidate is not None
    assert candidate.query == "Saturn"
    assert seen == ["Saturn rings Cassini", "Saturn"]


def test_arama_media_type_video_ile_yapilir():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == SEARCH_PATH:
            seen.update(dict(request.url.params))
            return httpx.Response(200, json=_search_payload())
        if request.url.path.endswith("collection.json"):
            return httpx.Response(200, json=["https://x/v~large.mp4"])
        return httpx.Response(200, content=b"x")

    _provider(handler).preview(_scene())
    assert seen["media_type"] == "video"


def test_ag_hatasi_aday_dondurmez():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("ağ yok")

    assert _provider(handler).preview(_scene()) is None


# --- indirme ---------------------------------------------------------------


def test_klip_indirilir(tmp_path):
    provider = _provider(_standard_handler())
    path = provider.generate(_scene(), tmp_path / "s01.mp4")

    assert path is not None
    assert path.read_bytes() == b"MP4-VERISI"
    assert provider.clips_generated == 1
    assert provider.skipped == []


def test_var_olan_klip_yeniden_indirilmez(tmp_path):
    destination = tmp_path / "s01.mp4"
    destination.write_bytes(b"onceden-indirilmis")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("ağa çıkılmamalıydı")

    provider = _provider(handler)
    assert provider.generate(_scene(), destination) == destination
    assert provider.clips_generated == 0


def test_eslesme_yoksa_none_doner_ve_sebep_raporlanir(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"collection": {"items": []}})

    provider = _provider(handler)
    assert provider.generate(_scene(), tmp_path / "s01.mp4") is None
    assert any("bulunamadı" in reason for reason in provider.skipped)


def test_buyuk_dosya_content_length_ile_atlanir(tmp_path):
    """Arşivde GB'larca master var; sekiz sahne diski doldurabilir."""
    provider = _provider(
        _standard_handler(content_length=str(DEFAULT_MAX_BYTES + 1)),
        max_bytes=DEFAULT_MAX_BYTES,
    )
    assert provider.generate(_scene(), tmp_path / "s01.mp4") is None
    assert any("çok büyük" in reason for reason in provider.skipped)


def test_content_length_yoksa_akis_sirasinda_sinir_uygulanir(tmp_path):
    """Sunucu boyut bildirmezse sınırı indirirken uygulamak zorundayız."""
    provider = _provider(_standard_handler(content=b"x" * 5000), max_bytes=1000)
    destination = tmp_path / "s01.mp4"

    assert provider.generate(_scene(), destination) is None
    assert not destination.exists(), "yarım dosya diskte bırakılmamalı"
    assert any("indirme başarısız" in reason for reason in provider.skipped)


def test_bos_indirme_reddedilir(tmp_path):
    provider = _provider(_standard_handler(content=b""))
    destination = tmp_path / "s01.mp4"

    assert provider.generate(_scene(), destination) is None
    assert not destination.exists()


def test_indirme_hatasi_pipelini_kirmaz(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == SEARCH_PATH:
            return httpx.Response(200, json=_search_payload())
        if request.url.path.endswith("collection.json"):
            return httpx.Response(200, json=["https://x/v~large.mp4"])
        if request.method == "HEAD":
            return httpx.Response(200)
        return httpx.Response(500)

    provider = _provider(handler)
    assert provider.generate(_scene(), tmp_path / "s01.mp4") is None
    assert provider.skipped


def test_hero_isareti_gerekmez(tmp_path):
    """Bedava bir kaynağı yıldız sahnelerle sınırlamak için sebep yok."""
    scene = _scene()
    assert scene.visual.hero is False
    provider = _provider(_standard_handler())
    assert provider.generate(scene, tmp_path / "s01.mp4") is not None


# --- boyut etiketi ---------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected"),
    [(None, "boyut bilinmiyor"), (0, "boyut bilinmiyor"), (2 * 1024 * 1024, "2.0 MB")],
)
def test_boyut_etiketi(size, expected):
    candidate = VideoCandidate(
        scene_id="s01", query="q", nasa_id="n", title="t", description="",
        url="https://x/v.mp4", size_bytes=size,
    )
    assert candidate.size_label == expected


# --- pipeline bağlantısı ----------------------------------------------------


def test_kaynak_nasa_secilince_arsiv_saglayicisi_kurulur(tmp_path):
    settings = Settings(workspace=tmp_path, offline=False)
    settings.clips.source = "nasa"
    provider = build_clip_provider(settings)
    assert isinstance(provider, NasaVideoProvider)
    assert provider.name == "nasa-video"


def test_varsayilan_kaynak_klip_kullanmaz(tmp_path):
    settings = Settings(workspace=tmp_path, offline=False)
    assert isinstance(build_clip_provider(settings), NullClipProvider)


def test_offline_modda_arsiv_saglayicisi_kurulmaz(tmp_path):
    settings = Settings(workspace=tmp_path, offline=True)
    settings.clips.source = "nasa"
    assert isinstance(build_clip_provider(settings), NullClipProvider)


def test_ai_kaynagi_anahtarsizken_devre_disi(tmp_path):
    settings = Settings(workspace=tmp_path, offline=False)
    settings.clips.source = "ai"
    settings.clips.enabled = True
    settings.clips.api_key = None
    assert isinstance(build_clip_provider(settings), NullClipProvider)


def test_bilinmeyen_kaynak_sessizce_devre_disi_birakir(tmp_path):
    settings = Settings(workspace=tmp_path, offline=False)
    settings.clips.source = "boyle-bir-kaynak-yok"
    assert isinstance(build_clip_provider(settings), NullClipProvider)


def test_ortam_degiskeni_kaynagi_secer(monkeypatch):
    monkeypatch.setenv("SPACEKIDS_CLIP_SOURCE", "nasa")
    assert Settings.load().clips.source == "nasa"


def test_ai_anahtari_kaynagi_ai_yapar(monkeypatch):
    monkeypatch.delenv("SPACEKIDS_CLIP_SOURCE", raising=False)
    monkeypatch.setenv("SEEDANCE_API_KEY", "k")
    settings = Settings.load()
    assert settings.clips.source == "ai"
    assert settings.clips.enabled is True


# --- CLI (ağa çıkmadan) -----------------------------------------------------


class _FakeNasaProvider:
    """CLI testleri için ağa çıkmayan sağlayıcı."""

    name = "nasa-video"

    def __init__(self, *, candidates=None, downloads=True, **kwargs) -> None:
        self._candidates = candidates
        self._downloads = downloads
        self.skipped: list[str] = []
        self.clips_generated = 0
        self.previewed: list[str] = []

    def preview(self, scene):
        self.previewed.append(scene.id)
        if self._candidates is None:
            return VideoCandidate(
                scene_id=scene.id, query=scene.visual.query, nasa_id="NASA-1",
                title="Fırlatma Görüntüsü", description="Roket kalkışı.",
                url="https://x/v~large.mp4", size_bytes=3 * 1024 * 1024,
            )
        return self._candidates.get(scene.id)

    def generate(self, scene, destination):
        if not self._downloads:
            self.skipped.append(f"{scene.id}: arşivde video bulunamadı")
            return None
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"MP4")
        self.clips_generated += 1
        return destination


def _install_fake(monkeypatch, **kwargs):
    import spacekids.providers.nasa_video as module

    monkeypatch.setattr(
        module, "NasaVideoProvider", lambda **_: _FakeNasaProvider(**kwargs)
    )


def test_cli_dry_run_bulunanlari_listeler(tmp_path, episode, monkeypatch):
    from typer.testing import CliRunner
    from spacekids.cli import app
    from spacekids.workspace import EpisodeWorkspace

    _install_fake(monkeypatch)
    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)

    result = CliRunner().invoke(
        app, ["clips", episode.id, "--dry-run", "--workspace", str(tmp_path)]
    )
    assert result.exit_code == 0, result.stdout
    assert "Prova çalıştırması" in result.stdout
    assert "Fırlatma Görüntüsü" in result.stdout
    assert "3.0 MB" in result.stdout
    assert "3/3 sahne" in result.stdout


def test_cli_eslesmeyeni_isaretler(tmp_path, episode, monkeypatch):
    from typer.testing import CliRunner
    from spacekids.cli import app
    from spacekids.workspace import EpisodeWorkspace

    _install_fake(monkeypatch, candidates={})
    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)

    result = CliRunner().invoke(
        app, ["clips", episode.id, "--dry-run", "--workspace", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "eşleşme yok" in result.stdout
    assert "0/3 sahne" in result.stdout


def test_cli_klipleri_indirir(tmp_path, episode, monkeypatch):
    from typer.testing import CliRunner
    from spacekids.cli import app
    from spacekids.workspace import EpisodeWorkspace

    _install_fake(monkeypatch)
    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)

    result = CliRunner().invoke(
        app, ["clips", episode.id, "--workspace", str(tmp_path)]
    )
    assert result.exit_code == 0, result.stdout
    assert "3/3 sahne için klip indirildi" in result.stdout
    assert (tmp_path / episode.id / "clips" / "s01.mp4").is_file()
    # Beğenilmeyen klibin silinebileceği kullanıcıya söylenmeli.
    assert "sil" in result.stdout


def test_cli_limit_sahne_sayisini_kisitlar(tmp_path, episode, monkeypatch):
    from typer.testing import CliRunner
    from spacekids.cli import app
    from spacekids.workspace import EpisodeWorkspace

    _install_fake(monkeypatch)
    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)

    result = CliRunner().invoke(
        app, ["clips", episode.id, "--limit", "1", "--workspace", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "1/1 sahne" in result.stdout


def test_cli_bilinmeyen_kaynak_reddedilir(tmp_path, episode):
    from typer.testing import CliRunner
    from spacekids.cli import app
    from spacekids.workspace import EpisodeWorkspace

    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)
    result = CliRunner().invoke(
        app, ["clips", episode.id, "--source", "youtube", "--workspace", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "Bilinmeyen kaynak" in result.stdout


def test_yapilandirma_dosyasi_ortam_degiskeniyle_verilebilir(tmp_path, monkeypatch):
    """CLI'ın ayarlarını testten ve kurulumdan yönlendirebilmek gerekiyor."""
    config = tmp_path / "ayarlar.toml"
    config.write_text("[video]\nwidth = 640\nheight = 360\n", encoding="utf-8")
    monkeypatch.setenv("SPACEKIDS_CONFIG", str(config))

    settings = Settings.load()
    assert settings.video.width == 640
    assert settings.video.height == 360
