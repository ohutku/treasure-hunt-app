"""YouTube'a yükleme.

Bir bölümün her dili **ayrı bir video** olarak yüklenir: video dosyası,
kapak görseli ve altyazı, `metadata.<dil>.json` içindeki başlık/açıklama/
etiketlerle birlikte.

**Varsayılan gizli.** Yükleme geri alması zor, dışarıya açılan bir işlem;
üstelik burada söz konusu olan çocuk içeriği. Bu yüzden video daima `private`
olarak gider ve herkese açık yapmak için hem açık bir bayrak hem de onay
gerekir. Yayına almadan önce videoyu YouTube Studio'da izlemen beklenir.

**Çocuklara yönelik işaretleme:** API'de yazılabilir alan
`status.selfDeclaredMadeForKids`'tir; `status.madeForKids` yalnızca okunur ve
yazmaya çalışmak sessizce yok sayılır. Bizim metadata dosyamız anlamsal olarak
`madeForKids` tutar, yükleme sırasında doğru alana eşlenir.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

#: Yalnızca video yüklemek için yeterli olan dar kapsam.
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
#: Kapak görseli ve altyazı için gereken geniş kapsam.
MANAGE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"

#: Yükleme parçalarının boyutu — büyük dosyalarda ilerleme raporu verebilmek için.
CHUNK_SIZE = 4 * 1024 * 1024

ALLOWED_PRIVACY = ("private", "unlisted", "public")


class YouTubeError(RuntimeError):
    """Yükleme akışı devam edemediğinde."""


@dataclass
class UploadPlan:
    """Tek bir dil için yüklenecek paket.

    `--dry-run` bunu basar: hiçbir istek gönderilmeden ne yükleneceğini
    görebilirsin.
    """

    episode_id: str
    language: str
    video_path: Path
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    category_id: str = "27"
    privacy: str = "private"
    made_for_kids: bool = True
    thumbnail_path: Path | None = None
    caption_path: Path | None = None
    duration_seconds: float = 0.0

    def to_body(self) -> dict[str, Any]:
        """YouTube API'sinin beklediği istek gövdesi."""
        return {
            "snippet": {
                "title": self.title,
                "description": self.description,
                "tags": self.tags,
                "categoryId": self.category_id,
                "defaultLanguage": self.language,
                "defaultAudioLanguage": self.language,
            },
            "status": {
                "privacyStatus": self.privacy,
                # Yazılabilir alan bu; `madeForKids` salt okunurdur.
                "selfDeclaredMadeForKids": self.made_for_kids,
            },
        }


def build_upload_plan(
    workspace,
    language: str,
    *,
    privacy: str = "private",
    include_thumbnail: bool = True,
    include_captions: bool = True,
) -> UploadPlan:
    """Render çıktılarından yükleme planını kurar."""
    if privacy not in ALLOWED_PRIVACY:
        raise YouTubeError(
            f"geçersiz gizlilik değeri: {privacy!r} (izin verilenler: {', '.join(ALLOWED_PRIVACY)})"
        )

    video_path = workspace.video_path(language)
    if not video_path.is_file():
        raise YouTubeError(
            f"{workspace.episode_id} [{language}]: video bulunamadı. "
            f"Önce 'spacekids render {workspace.episode_id}' çalıştır."
        )

    metadata_path = workspace.metadata_path(language)
    if not metadata_path.is_file():
        raise YouTubeError(f"{metadata_path} bulunamadı; bölümü yeniden render et.")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    thumbnail = workspace.thumbnail_path(language)
    caption = workspace.subtitle_path(language)

    return UploadPlan(
        episode_id=workspace.episode_id,
        language=language,
        video_path=video_path,
        title=metadata["title"],
        description=metadata["description"],
        tags=list(metadata.get("tags") or []),
        category_id=str(metadata.get("categoryId", "27")),
        privacy=privacy,
        made_for_kids=bool(metadata.get("madeForKids", True)),
        thumbnail_path=thumbnail if include_thumbnail and thumbnail.is_file() else None,
        caption_path=caption if include_captions and caption.is_file() else None,
        duration_seconds=float(metadata.get("durationSeconds") or 0.0),
    )


@dataclass
class UploadResult:
    """Bir yüklemenin sonucu."""

    episode_id: str
    language: str
    video_id: str
    url: str
    privacy: str
    thumbnail_set: bool = False
    caption_uploaded: bool = False
    #: Kapak/altyazı adımları başarısız olursa buraya yazılır — video yine yüklenmiştir.
    warnings: list[str] = field(default_factory=list)


class YouTubeUploader:
    """YouTube Data API v3 üzerinden yükleme.

    Servis nesnesi dışarıdan verilebilir; testler bunu sahte bir nesneyle
    değiştirir, böylece hiçbir test ağa çıkmaz.
    """

    def __init__(
        self,
        service: Any | None = None,
        *,
        client_secrets_file: Path | None = None,
        token_file: Path | None = None,
    ) -> None:
        self._service = service
        self.client_secrets_file = Path(client_secrets_file) if client_secrets_file else None
        self.token_file = Path(token_file) if token_file else None

    # --- kimlik doğrulama ------------------------------------------------

    def _load_credentials(self):
        """Kayıtlı jetonu okur, süresi dolmuşsa yeniler, yoksa tarayıcı akışı açar."""
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        scopes = [UPLOAD_SCOPE, MANAGE_SCOPE]
        credentials = None

        if self.token_file and self.token_file.is_file():
            credentials = Credentials.from_authorized_user_file(
                str(self.token_file), scopes
            )

        if credentials and credentials.valid:
            return credentials

        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not self.client_secrets_file or not self.client_secrets_file.is_file():
                raise YouTubeError(
                    "OAuth istemci dosyası bulunamadı. Google Cloud Console'dan "
                    "'OAuth client ID' (Desktop app) oluşturup JSON'u indir ve "
                    "yolunu --client-secrets ile ver."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.client_secrets_file), scopes
            )
            credentials = flow.run_local_server(port=0)

        if self.token_file:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(credentials.to_json(), encoding="utf-8")
            # Jeton dosyası kanal erişimi verir; yalnızca sahibi okuyabilsin.
            self.token_file.chmod(0o600)
        return credentials

    def service(self):
        """API istemcisini döndürür (gerekirse kimlik doğrulaması yaparak)."""
        if self._service is not None:
            return self._service
        try:
            from googleapiclient.discovery import build
        except ImportError as error:  # pragma: no cover - kurulum hatası
            raise YouTubeError(
                "YouTube istemcisi kurulu değil: pip install -e '.[youtube]'"
            ) from error
        self._service = build("youtube", "v3", credentials=self._load_credentials())
        return self._service

    # --- yükleme ---------------------------------------------------------

    def upload(
        self,
        plan: UploadPlan,
        *,
        on_progress: Callable[[int], None] | None = None,
    ) -> UploadResult:
        """Videoyu, ardından kapak ve altyazıyı yükler."""
        video_id = self._insert_video(plan, on_progress=on_progress)
        result = UploadResult(
            episode_id=plan.episode_id,
            language=plan.language,
            video_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            privacy=plan.privacy,
        )

        # Kapak ve altyazı ikincil adımlar: başarısız olurlarsa video yine
        # yüklenmiştir, bunu hata değil uyarı olarak raporlarız.
        if plan.thumbnail_path:
            try:
                self._set_thumbnail(video_id, plan.thumbnail_path)
                result.thumbnail_set = True
            except Exception as error:
                result.warnings.append(
                    f"kapak görseli yüklenemedi ({error}). Özel kapak için kanalının "
                    "doğrulanmış olması gerekir."
                )

        if plan.caption_path:
            try:
                self._insert_caption(video_id, plan.caption_path, plan.language)
                result.caption_uploaded = True
            except Exception as error:
                result.warnings.append(f"altyazı yüklenemedi ({error})")

        return result

    def _insert_video(
        self, plan: UploadPlan, *, on_progress: Callable[[int], None] | None = None
    ) -> str:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(
            str(plan.video_path),
            chunksize=CHUNK_SIZE,
            resumable=True,
            mimetype="video/mp4",
        )
        request = self.service().videos().insert(
            part="snippet,status", body=plan.to_body(), media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status and on_progress:
                on_progress(int(status.progress() * 100))

        video_id = (response or {}).get("id")
        if not video_id:
            raise YouTubeError(f"yanıtta video kimliği yok: {response}")
        if on_progress:
            on_progress(100)
        return str(video_id)

    def _set_thumbnail(self, video_id: str, path: Path) -> None:
        from googleapiclient.http import MediaFileUpload

        self.service().thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(path), mimetype="image/jpeg"),
        ).execute()

    def _insert_caption(self, video_id: str, path: Path, language: str) -> None:
        from googleapiclient.http import MediaFileUpload

        self.service().captions().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "language": language,
                    "name": f"{language.upper()} altyazı",
                    "isDraft": False,
                }
            },
            media_body=MediaFileUpload(str(path), mimetype="application/octet-stream"),
        ).execute()
