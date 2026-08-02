"""YouTube yükleme testleri — hiçbiri ağa çıkmaz."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from spacekids.cli import app
from spacekids.publish.youtube import (
    ALLOWED_PRIVACY,
    UploadPlan,
    YouTubeError,
    YouTubeUploader,
    build_upload_plan,
)
from spacekids.workspace import EpisodeWorkspace

runner = CliRunner()


# --- gerçek istemciyi taklit eden sahte servis ------------------------------


class _FakeRequest:
    """googleapiclient'ın sürdürülebilir yükleme isteğini taklit eder."""

    def __init__(self, response: dict, chunks: int = 2, fail: Exception | None = None) -> None:
        self._response = response
        self._remaining = chunks
        self._fail = fail

    def next_chunk(self):
        if self._fail:
            raise self._fail
        self._remaining -= 1
        if self._remaining > 0:
            return _FakeStatus(0.5), None
        return _FakeStatus(1.0), self._response

    def execute(self):
        if self._fail:
            raise self._fail
        return self._response


class _FakeStatus:
    def __init__(self, value: float) -> None:
        self._value = value

    def progress(self) -> float:
        return self._value


class FakeYouTubeService:
    """`service.videos().insert(...)` zincirini taklit eder ve çağrıları kaydeder."""

    def __init__(
        self,
        video_id: str = "vid-123",
        thumbnail_error: Exception | None = None,
        caption_error: Exception | None = None,
        insert_response: dict | None = None,
    ) -> None:
        self.video_id = video_id
        self.thumbnail_error = thumbnail_error
        self.caption_error = caption_error
        self.insert_response = insert_response
        self.insert_calls: list[dict] = []
        self.thumbnail_calls: list[str] = []
        self.caption_calls: list[dict] = []

    # videos().insert(...)
    def videos(self):
        return self

    def insert(self, part, body, media_body=None):
        self.insert_calls.append({"part": part, "body": body})
        response = (
            self.insert_response
            if self.insert_response is not None
            else {"id": self.video_id}
        )
        return _FakeRequest(response)

    # thumbnails().set(...)
    def thumbnails(self):
        return _FakeThumbnails(self)

    # captions().insert(...)
    def captions(self):
        return _FakeCaptions(self)


class _FakeThumbnails:
    def __init__(self, parent: FakeYouTubeService) -> None:
        self.parent = parent

    def set(self, videoId, media_body=None):
        self.parent.thumbnail_calls.append(videoId)
        return _FakeRequest({}, fail=self.parent.thumbnail_error)


class _FakeCaptions:
    def __init__(self, parent: FakeYouTubeService) -> None:
        self.parent = parent

    def insert(self, part, body, media_body=None):
        self.parent.caption_calls.append(body)
        return _FakeRequest({}, fail=self.parent.caption_error)


@pytest.fixture
def rendered(tmp_path, episode):
    """Render edilmiş gibi görünen bir bölüm klasörü kurar."""
    workspace = EpisodeWorkspace(tmp_path, episode.id)
    workspace.save_episode(episode)
    workspace.prepare(episode.languages)

    for language in episode.languages:
        workspace.video_path(language).write_bytes(b"sahte-mp4-verisi")
        workspace.thumbnail_path(language).write_bytes(b"sahte-jpeg")
        workspace.subtitle_path(language).write_text("1\n00:00:00,000 --> 00:00:02,000\nmerhaba\n", encoding="utf-8")
        workspace.save_metadata(
            language,
            {
                "title": f"Başlık {language}",
                "description": f"Açıklama {language}",
                "tags": ["uzay", "çocuk"],
                "categoryId": "27",
                "madeForKids": True,
                "durationSeconds": 55.5,
            },
        )
    return workspace


# --- plan kurma ------------------------------------------------------------


def test_plan_metadata_dosyasindan_kurulur(rendered):
    plan = build_upload_plan(rendered, "tr")
    assert plan.title == "Başlık tr"
    assert plan.tags == ["uzay", "çocuk"]
    assert plan.privacy == "private", "varsayılan gizli olmalı"
    assert plan.made_for_kids is True
    assert plan.thumbnail_path is not None
    assert plan.caption_path is not None
    assert plan.duration_seconds == 55.5


def test_plan_govdesi_yazilabilir_alani_kullanir(rendered):
    """`madeForKids` salt okunurdur; yazarken `selfDeclaredMadeForKids` gerekir."""
    body = build_upload_plan(rendered, "tr").to_body()
    assert body["status"]["selfDeclaredMadeForKids"] is True
    assert "madeForKids" not in body["status"]
    assert body["status"]["privacyStatus"] == "private"
    assert body["snippet"]["categoryId"] == "27"
    assert body["snippet"]["defaultLanguage"] == "tr"


def test_kapak_ve_altyazi_devre_disi_birakilabilir(rendered):
    plan = build_upload_plan(rendered, "tr", include_thumbnail=False, include_captions=False)
    assert plan.thumbnail_path is None
    assert plan.caption_path is None


def test_eksik_dosyalar_plana_konmaz(rendered):
    rendered.thumbnail_path("tr").unlink()
    rendered.subtitle_path("tr").unlink()
    plan = build_upload_plan(rendered, "tr")
    assert plan.thumbnail_path is None
    assert plan.caption_path is None


def test_render_edilmemis_bolum_reddedilir(tmp_path, episode):
    workspace = EpisodeWorkspace(tmp_path, episode.id)
    workspace.save_episode(episode)
    with pytest.raises(YouTubeError, match="video bulunamadı"):
        build_upload_plan(workspace, "tr")


def test_metadata_eksikse_anlamli_hata(rendered):
    rendered.metadata_path("tr").unlink()
    with pytest.raises(YouTubeError, match="bulunamadı"):
        build_upload_plan(rendered, "tr")


@pytest.mark.parametrize("privacy", ALLOWED_PRIVACY)
def test_gecerli_gizlilik_degerleri_kabul_edilir(rendered, privacy):
    assert build_upload_plan(rendered, "tr", privacy=privacy).privacy == privacy


def test_gecersiz_gizlilik_reddedilir(rendered):
    with pytest.raises(YouTubeError, match="geçersiz gizlilik"):
        build_upload_plan(rendered, "tr", privacy="herkese-acik")


# --- yükleme akışı ---------------------------------------------------------


def _plan(rendered, language: str = "tr", **kwargs) -> UploadPlan:
    return build_upload_plan(rendered, language, **kwargs)


def test_video_yuklenir_ve_kimlik_donulur(rendered):
    service = FakeYouTubeService(video_id="abc123")
    result = YouTubeUploader(service=service).upload(_plan(rendered))

    assert result.video_id == "abc123"
    assert result.url == "https://www.youtube.com/watch?v=abc123"
    assert service.insert_calls[0]["part"] == "snippet,status"
    assert result.thumbnail_set is True
    assert result.caption_uploaded is True
    assert result.warnings == []


def test_ilerleme_geri_cagrisi_calisir(rendered):
    percentages: list[int] = []
    YouTubeUploader(service=FakeYouTubeService()).upload(
        _plan(rendered), on_progress=percentages.append
    )
    assert percentages[-1] == 100
    assert percentages == sorted(percentages)


def test_altyazi_dogru_dille_yuklenir(rendered):
    service = FakeYouTubeService()
    YouTubeUploader(service=service).upload(_plan(rendered, "en"))
    assert service.caption_calls[0]["snippet"]["language"] == "en"
    assert service.caption_calls[0]["snippet"]["videoId"] == service.video_id


def test_kapak_hatasi_yuklemeyi_bozmaz(rendered):
    """Kapak ikincil bir adım; başarısız olsa da video yüklenmiş olur."""
    service = FakeYouTubeService(thumbnail_error=RuntimeError("kanal doğrulanmamış"))
    result = YouTubeUploader(service=service).upload(_plan(rendered))

    assert result.video_id  # video yüklendi
    assert result.thumbnail_set is False
    assert any("kapak" in warning for warning in result.warnings)
    assert result.caption_uploaded is True, "kapak hatası altyazıyı engellememeli"


def test_altyazi_hatasi_yuklemeyi_bozmaz(rendered):
    service = FakeYouTubeService(caption_error=RuntimeError("kota"))
    result = YouTubeUploader(service=service).upload(_plan(rendered))
    assert result.caption_uploaded is False
    assert any("altyazı" in warning for warning in result.warnings)


def test_kimliksiz_yanit_hata_verir(rendered):
    service = FakeYouTubeService(insert_response={"beklenmedik": "yapı"})
    with pytest.raises(YouTubeError, match="video kimliği"):
        YouTubeUploader(service=service).upload(_plan(rendered))


def test_kapak_ve_altyazi_yoksa_atlanir(rendered):
    service = FakeYouTubeService()
    plan = _plan(rendered, include_thumbnail=False, include_captions=False)
    result = YouTubeUploader(service=service).upload(plan)

    assert service.thumbnail_calls == []
    assert service.caption_calls == []
    assert result.thumbnail_set is False
    assert result.caption_uploaded is False


def test_istemci_dosyasi_yoksa_yol_gosterilir(tmp_path):
    uploader = YouTubeUploader(token_file=tmp_path / "yok.json")
    with pytest.raises(YouTubeError, match="OAuth istemci dosyası"):
        uploader._load_credentials()


# --- CLI -------------------------------------------------------------------


def test_dry_run_aga_cikmadan_paketi_gosterir(tmp_path, rendered, episode):
    result = runner.invoke(
        app, ["upload", episode.id, "--dry-run", "--workspace", str(tmp_path)]
    )
    assert result.exit_code == 0, result.stdout
    assert "Prova çalıştırması" in result.stdout
    assert "selfDeclaredMadeForKids=True" in result.stdout
    assert "private" in result.stdout
    # İki dil de listelenmeli.
    assert "[tr]" in result.stdout and "[en]" in result.stdout


def test_dry_run_dil_secimine_uyar(tmp_path, rendered, episode):
    result = runner.invoke(
        app,
        ["upload", episode.id, "--dry-run", "--languages", "tr", "--workspace", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "[tr]" in result.stdout and "[en]" not in result.stdout


def test_gecersiz_gizlilik_cli_de_reddedilir(tmp_path, rendered, episode):
    result = runner.invoke(
        app,
        ["upload", episode.id, "--privacy", "yarim-acik", "--workspace", str(tmp_path)],
    )
    assert result.exit_code == 1
    assert "Geçersiz gizlilik" in result.stdout


def test_render_edilmemis_bolum_cli_de_yol_gosterir(tmp_path, episode):
    EpisodeWorkspace(tmp_path, episode.id).save_episode(episode)
    result = runner.invoke(
        app, ["upload", episode.id, "--dry-run", "--workspace", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "spacekids render" in result.stdout


def test_herkese_acik_yukleme_onay_ister(tmp_path, rendered, episode):
    """Gizli olmayan yükleme geri alması zor; onaysız yapılmamalı."""
    result = runner.invoke(
        app,
        ["upload", episode.id, "--privacy", "public", "--workspace", str(tmp_path)],
        input="n\n",
    )
    assert result.exit_code == 1
    assert "gizli değil" in result.stdout
    assert "İptal edildi" in result.stdout
