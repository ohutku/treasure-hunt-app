"""AI klip katmanı: profiller, istem süzgeci ve sağlayıcı akışı."""

from __future__ import annotations

import httpx
import pytest
import yaml
from pydantic import ValidationError

from spacekids.config import PROJECT_ROOT, ClipSettings, Settings
from spacekids.models import Motion, Scene, SceneKind, Visual
from spacekids.providers.clip_profiles import (
    ClipProviderLibrary,
    ProfileError,
    get_path,
    load_clip_providers,
    render,
)
from spacekids.providers.clip_safety import (
    ClipPromptRejected,
    find_blocked_terms,
    guard_prompt,
    HARDENING_SUFFIX,
    MAX_PROMPT_CHARS,
)
from spacekids.providers.clips import ClipProvider, NullClipProvider

PROVIDERS_FILE = PROJECT_ROOT / "config" / "clip_providers.yaml"


@pytest.fixture(scope="module")
def library():
    return load_clip_providers(PROVIDERS_FILE)


def _scene(scene_id: str = "s01", *, hero: bool = True, prompt: str | None = None) -> Scene:
    return Scene(
        id=scene_id,
        kind=SceneKind.INTRO,
        visual=Visual(
            query="saturn rings",
            motion=Motion.ZOOM_IN,
            hero=hero,
            clip_prompt=prompt if prompt is not None else "a cartoon rocket passing Saturn",
        ),
        narration={"tr": "Satürn kocaman bir gezegendir ve halkaları vardır."},
    )


def _settings(**kwargs) -> ClipSettings:
    base = dict(enabled=True, api_key="test-key", timeout=5.0)
    base.update(kwargs)
    return ClipSettings(**base)


# =========================== yol ve şablon ==================================


@pytest.mark.parametrize(
    ("payload", "path", "expected"),
    [
        ({"id": "x"}, "id", "x"),
        ({"data": {"id": "x"}}, "data.id", "x"),
        ({"data": {"outputs": ["a", "b"]}}, "data.outputs.1", "b"),
        ({"data": {}}, "data.id", None),
        ({"data": None}, "data.id", None),
        ({"a": [1]}, "a.5", None),
        ({"a": "düz"}, "a.b", None),
    ],
)
def test_noktali_yol_okuma(payload, path, expected):
    assert get_path(payload, path) == expected


def test_sablon_ozyinelemeli_doldurulur():
    template = {"model": "{model}", "input": {"prompt": "{prompt} kısmı", "n": [1, "{model}"]}}
    result = render(template, {"model": "m1", "prompt": "roket"})
    assert result == {"model": "m1", "input": {"prompt": "roket kısmı", "n": [1, "m1"]}}


def test_tam_yer_tutucu_tipi_korur():
    """{duration} tek başınaysa sayı kalmalı — dizeye çevrilmemeli."""
    assert render("{duration}", {"duration": 5}) == 5
    assert render("süre: {duration}", {"duration": 5}) == "süre: 5"


def test_bilinmeyen_yer_tutucu_hata_verir():
    with pytest.raises(ProfileError, match="bilinmeyen yer tutucu"):
        render("{yok}", {"model": "m"})


# =========================== profiller ======================================


def test_depodaki_profiller_yuklenir(library):
    assert "byteplus" in library.ids()
    assert len(library.providers) >= 3


def test_her_profil_gerekli_alanlari_tasir(library):
    for profile in library.providers:
        assert profile.label and profile.base_url
        assert profile.default_model, profile.id
        assert profile.submit.task_id_paths or profile.submit.video_url_paths


def test_profiller_dogrulanmamis_olarak_isaretli(library):
    """Canlı anahtarla test edilmediler; bu durum açıkça görünmeli."""
    assert all(not profile.verified for profile in library.providers)


def test_bilinmeyen_profil_hata_verir(library):
    with pytest.raises(KeyError, match="bulunamadı"):
        library.get("yok-boyle-servis")


def test_profil_url_ve_basliklari_kurar(library):
    profile = library.get("byteplus")
    url = profile.url(profile.submit.path, {})
    assert url.startswith("https://") and "generations/tasks" in url
    assert profile.headers("k123") == {"Authorization": "Bearer k123"}


def test_piapi_farkli_kimlik_basligini_kullanir(library):
    assert library.get("piapi").headers("k123") == {"x-api-key": "k123"}


def test_eszamansiz_profil_poll_bolumu_ister():
    raw = {
        "providers": [
            {
                "id": "eksik",
                "label": "Eksik",
                "base_url": "https://x",
                "submit": {"path": "/t", "task_id_paths": ["id"]},
            }
        ]
    }
    with pytest.raises(ValidationError, match="poll"):
        ClipProviderLibrary.model_validate(raw)


def test_tekrarlanan_profil_kimligi_reddedilir():
    entry = {
        "id": "ayni",
        "label": "A",
        "base_url": "https://x",
        "submit": {"path": "/t", "video_url_paths": ["url"], "task_id_paths": ["id"]},
    }
    with pytest.raises(ValidationError, match="tekrar eden"):
        ClipProviderLibrary.model_validate({"providers": [entry, dict(entry)]})


def test_eksik_profil_dosyasi_anlamli_hata_verir(tmp_path):
    with pytest.raises(FileNotFoundError, match="klip sağlayıcı dosyası"):
        load_clip_providers(tmp_path / "yok.yaml")


def test_bozuk_profil_dosyasi_reddedilir(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(["liste"]), encoding="utf-8")
    with pytest.raises(ValueError, match="sözlük"):
        load_clip_providers(path)


# =========================== istem süzgeci ==================================


@pytest.mark.parametrize(
    "prompt",
    [
        "a Disney style rocket flying past Saturn",
        "Mickey Mouse waving from the moon",
        "a scene in the style of Pixar with planets",
        "Elsa and a spaceship above Jupiter",
        "a Paw Patrol pup exploring Mars",
        "Pikachu floating in zero gravity",
    ],
)
def test_telifli_karakter_istemleri_reddedilir(prompt):
    with pytest.raises(ClipPromptRejected, match="fikri mülkiyet"):
        guard_prompt(prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "a famous person walking on the moon",
        "an astronaut that looks like Neil Armstrong",
        "a copyrighted cartoon rocket",
    ],
)
def test_riskli_kaliplar_reddedilir(prompt):
    with pytest.raises(ClipPromptRejected):
        guard_prompt(prompt)


def test_temiz_istem_kisitlarla_sikilastirilir():
    hardened = guard_prompt("a friendly cartoon rocket gliding past Saturn's rings")
    assert hardened.startswith("a friendly cartoon rocket")
    assert HARDENING_SUFFIX in hardened
    assert "No recognizable cartoon characters" in hardened


def test_bos_istem_reddedilir():
    with pytest.raises(ClipPromptRejected, match="boş istem"):
        guard_prompt("   ")


def test_uzun_istem_kirpilirken_kisitlar_korunur():
    hardened = guard_prompt("bir roket " * 400)
    assert len(hardened) <= MAX_PROMPT_CHARS
    assert HARDENING_SUFFIX in hardened, "kırpma kısıtları asla düşürmemeli"


def test_kelime_siniri_yanlis_alarm_vermez():
    """'cars' geçen masum bir istem 'Cars' filmine takılmamalı."""
    assert find_blocked_terms("toy cars on a table") == []
    assert find_blocked_terms("a scene from Toy Story") == ["Toy Story"]


def test_depodaki_hero_istemleri_suzgecten_gecer(topics):
    """Kütüphanedeki hazır istemler süzgece takılmamalı."""
    for topic in topics.topics:
        if topic.hero_prompt:
            assert guard_prompt(topic.hero_prompt)


# =========================== sağlayıcı akışı ================================


def test_kapali_saglayici_klip_uretmez(tmp_path, library):
    provider = ClipProvider(_settings(enabled=False), library.get("byteplus"))
    assert provider.generate(_scene(), tmp_path / "s01.mp4") is None


def test_anahtarsiz_saglayici_klip_uretmez(tmp_path, library):
    provider = ClipProvider(_settings(api_key=None), library.get("byteplus"))
    assert provider.is_available() is False
    assert provider.generate(_scene(), tmp_path / "s01.mp4") is None


def test_hero_olmayan_sahne_atlanir(tmp_path, library):
    provider = ClipProvider(_settings(), library.get("byteplus"))
    assert provider.generate(_scene(hero=False), tmp_path / "s01.mp4") is None


def test_kota_uygulanir(tmp_path, library):
    provider = ClipProvider(_settings(max_clips_per_episode=2), library.get("byteplus"))
    provider.clips_generated = 2
    assert provider.quota_remaining == 0
    assert provider.generate(_scene(), tmp_path / "s01.mp4") is None
    assert any("kota" in reason for reason in provider.skipped)


def test_var_olan_klip_kotadan_dusmez(tmp_path, library):
    destination = tmp_path / "s01.mp4"
    destination.write_bytes(b"onceden")
    provider = ClipProvider(_settings(), library.get("byteplus"))
    assert provider.generate(_scene(), destination) == destination
    assert provider.clips_generated == 0


def test_reddedilen_istem_uretimi_durdurur_ama_kirmaz(tmp_path, library):
    provider = ClipProvider(_settings(), library.get("byteplus"))
    scene = _scene(prompt="a Disney princess on Mars")
    assert provider.generate(scene, tmp_path / "s01.mp4") is None
    assert any("fikri mülkiyet" in reason for reason in provider.skipped)


def test_prepare_aga_cikmadan_istegi_kurar(library):
    provider = ClipProvider(_settings(clip_seconds=5, resolution="720p"), library.get("byteplus"))
    request = provider.prepare(_scene())
    assert request.url.endswith("/contents/generations/tasks")
    assert request.body["model"] == "seedance-2-5"
    assert "--duration 5" in request.body["content"][0]["text"]
    assert HARDENING_SUFFIX in request.prompt


def test_model_ayarla_gecersiz_kilinabilir(library):
    provider = ClipProvider(_settings(model="özel-model"), library.get("byteplus"))
    assert provider.prepare(_scene()).body["model"] == "özel-model"


def _mock_provider(library, profile_id: str, handler) -> ClipProvider:
    return ClipProvider(
        _settings(),
        library.get(profile_id),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_byteplus_akisi_ucdan_uca(tmp_path, library):
    """Gönder → durumu sor → sonucu indir."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(200, json={"id": "task-1"})
        if "generations/tasks/task-1" in request.url.path:
            return httpx.Response(
                200,
                json={"status": "succeeded", "content": {"video_url": "https://x/v.mp4"}},
            )
        return httpx.Response(200, content=b"MP4-VERISI")

    provider = _mock_provider(library, "byteplus", handler)
    path = provider.generate(_scene(), tmp_path / "s01.mp4")

    assert path is not None and path.read_bytes() == b"MP4-VERISI"
    assert provider.clips_generated == 1
    assert seen[0].startswith("POST")


def test_fal_akisi_ayri_sonuc_uc_noktasini_kullanir(tmp_path, library):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": "req-9"})
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        if request.url.path.endswith("/requests/req-9"):
            return httpx.Response(200, json={"video": {"url": "https://x/v.mp4"}})
        return httpx.Response(200, content=b"FAL-VERISI")

    provider = _mock_provider(library, "fal", handler)
    path = provider.generate(_scene(), tmp_path / "s01.mp4")
    assert path is not None and path.read_bytes() == b"FAL-VERISI"


def test_wavespeed_ic_ice_yanit_yapisini_cozer(tmp_path, library):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"id": "w-1"}})
        if "predictions" in request.url.path:
            return httpx.Response(
                200,
                json={"data": {"status": "completed", "outputs": ["https://x/v.mp4"]}},
            )
        return httpx.Response(200, content=b"WS-VERISI")

    provider = _mock_provider(library, "wavespeed", handler)
    assert provider.generate(_scene(), tmp_path / "s01.mp4").read_bytes() == b"WS-VERISI"


def test_gorev_basarisiz_olursa_sebep_raporlanir(tmp_path, library):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "task-1"})
        return httpx.Response(
            200, json={"status": "failed", "error": {"message": "içerik reddedildi"}}
        )

    provider = _mock_provider(library, "byteplus", handler)
    assert provider.generate(_scene(), tmp_path / "s01.mp4") is None
    assert any("içerik reddedildi" in reason for reason in provider.skipped)
    assert provider.clips_generated == 0


def test_sunucu_hatasi_pipelini_kirmaz(tmp_path, library):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "sunucu hatası"})

    provider = _mock_provider(library, "byteplus", handler)
    assert provider.generate(_scene(), tmp_path / "s01.mp4") is None
    assert provider.skipped


def test_gorev_kimligi_yoksa_anlamli_hata(tmp_path, library):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"beklenmedik": "yapı"})

    provider = _mock_provider(library, "byteplus", handler)
    assert provider.generate(_scene(), tmp_path / "s01.mp4") is None
    assert any("görev kimliği" in reason for reason in provider.skipped)


def test_bos_video_indirmesi_reddedilir(tmp_path, library):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "t"})
        if "generations/tasks/t" in request.url.path:
            return httpx.Response(
                200, json={"status": "succeeded", "content": {"video_url": "https://x/v.mp4"}}
            )
        return httpx.Response(200, content=b"")

    provider = _mock_provider(library, "byteplus", handler)
    destination = tmp_path / "s01.mp4"
    assert provider.generate(_scene(), destination) is None
    assert not destination.exists(), "boş dosya diskte bırakılmamalı"


def test_zaman_asimi_uretimi_durdurur(tmp_path, library):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "t"})
        return httpx.Response(200, json={"status": "running"})

    provider = ClipProvider(
        _settings(timeout=0.0),
        library.get("byteplus"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert provider.generate(_scene(), tmp_path / "s01.mp4") is None
    assert any("tamamlanmadı" in reason for reason in provider.skipped)


def test_null_saglayici_daima_none_doner(tmp_path):
    assert NullClipProvider().generate(_scene(), tmp_path / "x.mp4") is None


# =========================== ayarlar ========================================


def test_ortam_degiskeni_klip_uretimini_acar(monkeypatch):
    monkeypatch.setenv("SEEDANCE_API_KEY", "env-key")
    monkeypatch.setenv("SPACEKIDS_CLIP_PROVIDER", "fal")
    settings = Settings.load()
    assert settings.clips.api_key == "env-key"
    assert settings.clips.enabled is True
    assert settings.clips.provider == "fal"


def test_anahtar_yoksa_klip_uretimi_kapali(monkeypatch):
    for var in ("SPACEKIDS_CLIP_API_KEY", "SEEDANCE_API_KEY", "BYTEPLUS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert Settings.load().clips.enabled is False


def test_from_settings_profili_cozer():
    provider = ClipProvider.from_settings(_settings(provider="piapi"), PROVIDERS_FILE)
    assert provider.profile.id == "piapi"
    assert provider.name == "clip:piapi"
