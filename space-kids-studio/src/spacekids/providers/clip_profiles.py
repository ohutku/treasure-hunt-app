"""Klip sağlayıcı profilleri.

Seedance'e erişim sağlayan siteler hızla değişiyor: ByteDance Mart 2026'da
uluslararası API'yi askıya aldı, Temmuz'da 2.5 ile yeniden açtı, aradaki
boşlukta bir sürü aracı servis türedi. Wire formatını Python'a gömmek bu
ortamda yanlış olurdu — bir sağlayıcı kapandığında ya da alan adını
değiştirdiğinde kod değişmek zorunda kalırdı.

Bu yüzden istek/yanıt şekilleri `config/clip_providers.yaml` içinde
**veri olarak** duruyor. Yeni bir servise geçmek için bir YAML bloğu
eklemek yeterli; Python'a dokunmaya gerek yok.

Profil üç şeyi tarif eder:

1. **submit** — görevi nasıl gönderiyoruz, görev kimliği yanıtın neresinde
2. **poll**   — durumu nasıl soruyoruz, hangi değerler başarı/başarısızlık
3. **result** — video bağlantısı yanıtın neresinde
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Şablonlardaki {yer_tutucu} deseni.
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class ProfileError(RuntimeError):
    """Profil tanımı ya da sunucu yanıtı beklenenle uyuşmadığında."""


def get_path(payload: Any, path: str) -> Any:
    """Noktalı yoldan iç içe değeri okur.

    Liste indeksleri de desteklenir: ``data.outputs.0`` geçerli bir yoldur.
    Yol bulunamazsa None döner — sağlayıcılar alanları farklı yerlere
    koyduğu için birden çok yolu sırayla denemek istiyoruz.
    """
    current = payload
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            if not part.isdigit() or int(part) >= len(current):
                return None
            current = current[int(part)]
        else:
            return None
    return current


def render(value: Any, context: dict[str, Any]) -> Any:
    """Şablondaki yer tutucuları bağlamdaki değerlerle doldurur.

    Sözlük ve listelerin içinde özyinelemeli çalışır. Bir dize tamamen tek
    bir yer tutucudan ibaretse (``"{duration}"``), değer **tipi korunarak**
    yerleştirilir — böylece sayı isteyen alanlar dizeye dönüşmez.
    """
    if isinstance(value, str):
        exact = _PLACEHOLDER.fullmatch(value)
        if exact:
            if exact.group(1) not in context:
                raise ProfileError(f"şablonda bilinmeyen yer tutucu: {value}")
            return context[exact.group(1)]

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in context:
                raise ProfileError(f"şablonda bilinmeyen yer tutucu: {{{key}}}")
            return str(context[key])

        return _PLACEHOLDER.sub(replace, value)

    if isinstance(value, dict):
        return {key: render(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, context) for item in value]
    return value


class SubmitSpec(BaseModel):
    """Görev gönderme adımı."""

    model_config = ConfigDict(extra="forbid")

    method: str = "POST"
    path: str
    body: dict[str, Any] = Field(default_factory=dict)
    #: Görev kimliğinin yanıttaki yolu; birden fazla aday denenebilir.
    task_id_paths: list[str] = Field(min_length=1)
    #: Yanıt görevi değil doğrudan videoyu döndürüyorsa (senkron servisler).
    video_url_paths: list[str] = Field(default_factory=list)


class PollSpec(BaseModel):
    """Görev durumunu sorgulama adımı."""

    model_config = ConfigDict(extra="forbid")

    path: str
    status_paths: list[str] = Field(min_length=1)
    succeeded: list[str] = Field(min_length=1)
    failed: list[str] = Field(default_factory=list)
    #: Hata mesajının yanıttaki yolu — kullanıcıya anlamlı bilgi verebilmek için.
    error_paths: list[str] = Field(default_factory=list)


class ResultSpec(BaseModel):
    """Video bağlantısını okuma adımı."""

    model_config = ConfigDict(extra="forbid")

    #: Ayrı bir sonuç uç noktası varsa (fal.ai böyle çalışır).
    path: str | None = None
    video_url_paths: list[str] = Field(min_length=1)


class ClipProviderProfile(BaseModel):
    """Tek bir klip servisinin API tarifi."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    base_url: str
    #: Servisin kendi sayfası — kullanıcı anahtarı buradan alır.
    signup_url: str = ""
    #: Ücretsiz kullanım hakkında kısa not (CLI'da gösterilir).
    free_tier: str = ""
    #: Kimlik doğrulama başlığı ve değeri; {key} anahtarla değiştirilir.
    auth_header: str = "Authorization"
    auth_value: str = "Bearer {key}"
    default_model: str = ""
    submit: SubmitSpec
    poll: PollSpec | None = None
    result: ResultSpec | None = None
    #: Bu profilin gerçek bir anahtarla doğrulanıp doğrulanmadığı.
    verified: bool = False
    notes: str = ""

    @model_validator(mode="after")
    def _validate_flow(self) -> ClipProviderProfile:
        synchronous = bool(self.submit.video_url_paths)
        if not synchronous and self.poll is None:
            raise ValueError(
                f"{self.id}: eşzamansız profil için 'poll' bölümü gerekli"
            )
        if not synchronous and self.result is None:
            raise ValueError(
                f"{self.id}: eşzamansız profil için 'result' bölümü gerekli"
            )
        return self

    @property
    def is_synchronous(self) -> bool:
        """Servis videoyu tek istekte döndürüyor mu?"""
        return bool(self.submit.video_url_paths)

    def headers(self, api_key: str) -> dict[str, str]:
        return {self.auth_header: render(self.auth_value, {"key": api_key})}

    def url(self, path: str, context: dict[str, Any]) -> str:
        return f"{self.base_url.rstrip('/')}/{render(path, context).lstrip('/')}"

    def first_value(self, payload: Any, paths: list[str]) -> Any:
        """Aday yolları sırayla dener, ilk dolu değeri döndürür."""
        for path in paths:
            value = get_path(payload, path)
            if value not in (None, "", [], {}):
                return value
        return None


class ClipProviderLibrary(BaseModel):
    """clip_providers.yaml'ın tamamı."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    providers: list[ClipProviderProfile] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique(self) -> ClipProviderLibrary:
        ids = [profile.id for profile in self.providers]
        duplicates = sorted({pid for pid in ids if ids.count(pid) > 1})
        if duplicates:
            raise ValueError(f"tekrar eden sağlayıcı kimlikleri: {duplicates}")
        return self

    def get(self, provider_id: str) -> ClipProviderProfile:
        for profile in self.providers:
            if profile.id == provider_id:
                return profile
        available = ", ".join(profile.id for profile in self.providers)
        raise KeyError(
            f"klip sağlayıcısı bulunamadı: {provider_id!r}. Mevcut: {available}"
        )

    def ids(self) -> list[str]:
        return [profile.id for profile in self.providers]


def load_clip_providers(path: Path) -> ClipProviderLibrary:
    """Sağlayıcı profillerini YAML'dan yükler."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"klip sağlayıcı dosyası bulunamadı: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: kök seviyede bir sözlük bekleniyordu")
    return ClipProviderLibrary.model_validate(raw)
