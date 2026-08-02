"""Sağlayıcı katmanı testleri (ağa çıkmadan)."""

from __future__ import annotations

import httpx
import pytest

from spacekids.models import Motion, Scene, SceneKind, Visual
from spacekids.providers.fake import FailingImageProvider, FailingVoiceProvider
from spacekids.providers.images import (
    FallbackImageProvider,
    NasaImageProvider,
    ProceduralImageProvider,
    normalize,
)
from spacekids.providers.voice import FallbackVoiceProvider, SilentVoiceProvider, configure_tls
from spacekids.media.ffmpeg import probe_duration

from PIL import Image


def _scene(scene_id: str = "s01", *, hero: bool = False) -> Scene:
    return Scene(
        id=scene_id,
        kind=SceneKind.FACT,
        visual=Visual(
            query="saturn rings",
            fallback_queries=["saturn"],
            motion=Motion.ZOOM_IN,
            hero=hero,
            clip_prompt="a cartoon rocket flying past Saturn" if hero else None,
        ),
        narration={"tr": "Satürn kocaman bir gezegendir ve halkaları vardır."},
    )


# --- görsel ----------------------------------------------------------------


def test_prosedurel_gorsel_uretilir(tmp_path):
    provider = ProceduralImageProvider(size=(320, 180))
    path = provider.fetch(_scene(), tmp_path / "s01.jpg")
    assert path.is_file()
    with Image.open(path) as image:
        assert image.size == (320, 180)


def test_prosedurel_gorsel_deterministiktir():
    provider = ProceduralImageProvider(size=(160, 90))
    first = provider.render("saturn rings").tobytes()
    second = provider.render("saturn rings").tobytes()
    assert first == second


def test_farkli_sorgu_farkli_gorsel_verir():
    provider = ProceduralImageProvider(size=(160, 90))
    assert provider.render("saturn").tobytes() != provider.render("mars").tobytes()


def test_var_olan_gorsel_yeniden_uretilmez(tmp_path):
    provider = ProceduralImageProvider(size=(160, 90))
    destination = tmp_path / "s01.jpg"
    provider.fetch(_scene(), destination)
    first_mtime = destination.stat().st_mtime_ns
    provider.fetch(_scene(), destination)
    assert destination.stat().st_mtime_ns == first_mtime


@pytest.mark.parametrize("source_size", [(4000, 1000), (800, 2000), (1920, 1080)])
def test_normalize_hedef_boyuta_getirir(source_size):
    result = normalize(Image.new("RGB", source_size, "blue"), (640, 360))
    assert result.size == (640, 360)


def test_gorsel_yedegine_dusulur(tmp_path):
    primary = FailingImageProvider()
    fallback = ProceduralImageProvider(size=(160, 90))
    provider = FallbackImageProvider(primary, fallback)

    path = provider.fetch(_scene(), tmp_path / "s01.jpg")
    assert path.is_file()
    assert primary.calls == 1
    assert provider.fallback_scenes == ["s01"]


def test_nasa_saglayicisi_sahte_yanitla_calisir(tmp_path):
    """NASA akışını gerçek ağa çıkmadan, sahte taşıma katmanıyla doğrular."""
    buffer = tmp_path / "kaynak.jpg"
    Image.new("RGB", (1200, 900), "navy").save(buffer)
    image_bytes = buffer.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={
                    "collection": {
                        "items": [
                            {
                                "href": "https://images-assets.nasa.gov/x/collection.json",
                                "links": [{"href": "https://x/preview.jpg", "render": "image"}],
                            }
                        ]
                    }
                },
            )
        if request.url.path.endswith("collection.json"):
            return httpx.Response(
                200, json=["https://images-assets.nasa.gov/x/photo~large.jpg"]
            )
        return httpx.Response(200, content=image_bytes)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = NasaImageProvider(size=(640, 360), client=client)

    path = provider.fetch(_scene(), tmp_path / "s01.jpg")
    with Image.open(path) as image:
        assert image.size == (640, 360)


def test_nasa_sonuc_bulamazsa_hata_verir(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"collection": {"items": []}})

    provider = NasaImageProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(Exception, match="bulunamadı"):
        provider.fetch(_scene(), tmp_path / "s01.jpg")


# --- ses -------------------------------------------------------------------


def test_sessiz_ses_tahmini_sureyi_tutturur(tmp_path):
    provider = SilentVoiceProvider()
    text = "Satürn kocaman bir gezegendir. Etrafında buzdan halkaları vardır."
    path = provider.synthesize(text, "tr", tmp_path / "s01.mp3")
    duration = probe_duration(path)
    assert 2.0 < duration < 10.0


def test_ses_yedegine_dusulur(tmp_path):
    provider = FallbackVoiceProvider(FailingVoiceProvider(), SilentVoiceProvider())
    path = provider.synthesize("Satürn büyüktür ve halkaları vardır.", "tr", tmp_path / "a.mp3")
    assert path.is_file()
    assert provider.fallback_count == 1


def test_tls_yapilandirmasi_ca_yoksa_sessizce_gecer(monkeypatch):
    for var in ("SPACEKIDS_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        monkeypatch.delenv(var, raising=False)

    class Module:
        _SSL_CTX = None

    assert configure_tls(Module) is False
