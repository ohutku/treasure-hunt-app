"""Yaşa uygunluk denetimi.

Senaryo ister şablondan ister bir dil modelinden gelsin, videoya dönüşmeden
önce buradan geçer. Amaç iki türlü hatayı yakalamak:

* **İçerik** — küçük çocukları korkutacak veya uygunsuz temalar, izleyiciyi
  yönlendiren satış/link ifadeleri.
* **Biçim** — çok uzun cümleler, çok kısa/uzun sahneler, hedeflenen video
  süresinden sapma.

Denetim iki seviyeli: `ERROR` render'ı durdurur, `WARNING` sadece uyarır.
Uzay konusu doğası gereği "patlama", "çarpışma" gibi kelimeler içerebildiği
için bunlar hata değil uyarı olarak işaretlenir.
"""

from __future__ import annotations

import re
from enum import Enum
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field

from ..models import Episode

#: Cümle sonu işaretleri.
_SENTENCE_SPLIT = re.compile(r"[.!?…]+(?:\s+|$)")
#: Kesme işareti kelimeyi bölmemeli: "Satürn'ü" ve "let's" tek kelime sayılır.
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)
_URL = re.compile(r"(https?://|www\.|\.com\b|\.net\b)", re.IGNORECASE)

# Terim listelerinde sondaki `*` "bu kökle başlayan her kelime" demektir.
# Yıldızsız terimler tam kelime olarak aranır. Bu ayrım kritik: Türkçe eklemeli
# bir dil olduğu için "öldür*" ekleri yakalamalı, ama düz alt-dize araması
# "bölüm" içinde "ölüm", "kanatları" içinde "kan" bulur ve yanlış alarm verir.

#: Kesinlikle bulunmaması gereken temalar.
BANNED_TERMS: dict[str, tuple[str, ...]] = {
    "tr": (
        "öldür*", "ölüm", "ölümü", "ölümün", "cinayet*", "silah*", "bomba*",
        "savaş*", "kan", "kanı", "kanama*", "korkunç*", "dehşet*", "canavar*",
        "hayalet*", "kabus*", "işkence*", "nefret*", "aptal*", "salak*", "çirkin*",
    ),
    "en": (
        "kill*", "death*", "dead", "die", "dies", "dying", "murder*", "weapon*",
        "gun", "guns", "war", "wars", "blood*", "horror*", "terrifying",
        "monster*", "ghost*", "nightmare*", "torture*", "hate*", "stupid",
        "idiot*", "ugly",
    ),
}

#: Uzay bağlamında meşru ama tonu sertleştirebilecek kelimeler — uyarı üretir.
CAUTION_TERMS: dict[str, tuple[str, ...]] = {
    "tr": ("patla*", "çarpış*", "yok ol*", "tehlike*", "yutar", "yutuyor", "parçalan*"),
    "en": (
        "explode*", "explosion*", "crash*", "collide*", "collision*",
        "destroy*", "danger*", "swallow*",
    ),
}

#: İzleyiciyi ticari yönlendiren ifadeler — çocuk içeriğinde yasak.
COMMERCIAL_TERMS: dict[str, tuple[str, ...]] = {
    "tr": ("satın al*", "sipariş ver*", "indirim*", "hemen al*", "linke tıkla*", "tıkla ve"),
    "en": ("buy now", "order now", "discount*", "click the link", "click here", "swipe up"),
}


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class LanguageProfile(BaseModel):
    """Bir dil için okunabilirlik eşikleri.

    Türkçe eklemeli bir dil olduğu için aynı anlamı daha az kelimeyle ifade eder;
    bu yüzden kelime eşikleri İngilizceden düşüktür.
    """

    model_config = ConfigDict(extra="forbid")

    max_sentence_words: int
    max_average_sentence_words: float
    #: Seslendirme hızı tahmini (dakikadaki kelime) — süre kestirimi için.
    words_per_minute: float


LANGUAGE_PROFILES: dict[str, LanguageProfile] = {
    "tr": LanguageProfile(
        max_sentence_words=13, max_average_sentence_words=10.0, words_per_minute=115.0
    ),
    "en": LanguageProfile(
        max_sentence_words=17, max_average_sentence_words=13.0, words_per_minute=130.0
    ),
}

DEFAULT_PROFILE = LanguageProfile(
    max_sentence_words=16, max_average_sentence_words=12.0, words_per_minute=125.0
)

#: Sahne başına kelime aralığı — çok kısa sahne akışı bozar, çok uzunu sıkar.
MIN_SCENE_WORDS = 6
MAX_SCENE_WORDS = 60

#: Hedeflenen toplam video süresi (saniye). Dışına çıkmak uyarı üretir.
MIN_TARGET_SECONDS = 45.0
MAX_TARGET_SECONDS = 600.0


class SafetyIssue(BaseModel):
    """Tek bir denetim bulgusu."""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Severity
    message: str
    language: str | None = None
    scene_id: str | None = None

    def format(self) -> str:
        where = " ".join(part for part in (self.language, self.scene_id) if part)
        prefix = f"[{self.severity.value.upper()}] {self.code}"
        return f"{prefix} ({where}): {self.message}" if where else f"{prefix}: {self.message}"


class SafetyReport(BaseModel):
    """Bir bölümün denetim sonucu."""

    model_config = ConfigDict(extra="forbid")

    episode_id: str
    issues: list[SafetyIssue] = Field(default_factory=list)
    #: dil -> tahmini süre (saniye)
    estimated_durations: dict[str, float] = Field(default_factory=dict)

    @property
    def errors(self) -> list[SafetyIssue]:
        return [issue for issue in self.issues if issue.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[SafetyIssue]:
        return [issue for issue in self.issues if issue.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        """Hata yoksa render'a devam edilebilir."""
        return not self.errors

    def summary(self) -> str:
        return f"{len(self.errors)} hata, {len(self.warnings)} uyarı"


def split_sentences(text: str) -> list[str]:
    """Metni cümlelere ayırır (boş parçalar atılır)."""
    return [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]


def count_words(text: str) -> int:
    return len(_WORD.findall(text))


def estimate_duration(text: str, language: str) -> float:
    """Anlatım metninin kaç saniye süreceğini kabaca kestirir."""
    profile = LANGUAGE_PROFILES.get(language, DEFAULT_PROFILE)
    return count_words(text) / profile.words_per_minute * 60.0


@lru_cache(maxsize=256)
def _compile_term(term: str) -> re.Pattern[str]:
    """Terimi kelime sınırına saygılı bir düzenli ifadeye çevirir."""
    if term.endswith("*"):
        body = re.escape(term[:-1]) + r"\w*"
    else:
        body = re.escape(term) + r"\b"
    return re.compile(rf"\b{body}", re.IGNORECASE | re.UNICODE)


def _find_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    """Metinde geçen yasaklı/dikkatli terimlerin gerçek yazımlarını döndürür."""
    found: list[str] = []
    seen: set[str] = set()
    for term in terms:
        match = _compile_term(term).search(text)
        if match:
            surface = match.group(0).casefold()
            if surface not in seen:
                seen.add(surface)
                found.append(match.group(0))
    return found


def check_episode(episode: Episode) -> SafetyReport:
    """Bölümün tüm dillerini denetler."""
    report = SafetyReport(episode_id=episode.id)

    for language in episode.languages:
        profile = LANGUAGE_PROFILES.get(language, DEFAULT_PROFILE)
        total_seconds = 0.0
        all_sentences: list[str] = []

        for scene in episode.scenes:
            text = scene.text(language)
            total_seconds += estimate_duration(text, language)
            sentences = split_sentences(text)
            all_sentences.extend(sentences)
            report.issues.extend(_check_scene_text(text, sentences, profile, language, scene.id))

        localization = episode.localizations[language]
        report.issues.extend(_check_localization(localization, language))

        if all_sentences:
            average = sum(count_words(s) for s in all_sentences) / len(all_sentences)
            if average > profile.max_average_sentence_words:
                report.issues.append(
                    SafetyIssue(
                        code="reading_level",
                        severity=Severity.WARNING,
                        language=language,
                        message=(
                            f"ortalama cümle uzunluğu {average:.1f} kelime, "
                            f"hedef en fazla {profile.max_average_sentence_words}"
                        ),
                    )
                )

        report.estimated_durations[language] = round(total_seconds, 1)
        if total_seconds < MIN_TARGET_SECONDS:
            report.issues.append(
                SafetyIssue(
                    code="too_short",
                    severity=Severity.WARNING,
                    language=language,
                    message=f"tahmini süre {total_seconds:.0f} sn, {MIN_TARGET_SECONDS:.0f} sn'den kısa",
                )
            )
        elif total_seconds > MAX_TARGET_SECONDS:
            report.issues.append(
                SafetyIssue(
                    code="too_long",
                    severity=Severity.WARNING,
                    language=language,
                    message=f"tahmini süre {total_seconds:.0f} sn, {MAX_TARGET_SECONDS:.0f} sn'yi aşıyor",
                )
            )

    return report


def _check_scene_text(
    text: str,
    sentences: list[str],
    profile: LanguageProfile,
    language: str,
    scene_id: str,
) -> list[SafetyIssue]:
    issues: list[SafetyIssue] = []

    banned = _find_terms(text, BANNED_TERMS.get(language, ()))
    if banned:
        issues.append(
            SafetyIssue(
                code="banned_term",
                severity=Severity.ERROR,
                language=language,
                scene_id=scene_id,
                message=f"çocuk içeriğine uygun olmayan ifade(ler): {', '.join(banned)}",
            )
        )

    caution = _find_terms(text, CAUTION_TERMS.get(language, ()))
    if caution:
        issues.append(
            SafetyIssue(
                code="caution_term",
                severity=Severity.WARNING,
                language=language,
                scene_id=scene_id,
                message=f"tonu sertleştirebilecek ifade(ler): {', '.join(caution)}",
            )
        )

    commercial = _find_terms(text, COMMERCIAL_TERMS.get(language, ()))
    if commercial:
        issues.append(
            SafetyIssue(
                code="commercial_language",
                severity=Severity.ERROR,
                language=language,
                scene_id=scene_id,
                message=f"çocuklara yönelik ticari yönlendirme: {', '.join(commercial)}",
            )
        )

    if _URL.search(text):
        issues.append(
            SafetyIssue(
                code="url_in_narration",
                severity=Severity.ERROR,
                language=language,
                scene_id=scene_id,
                message="anlatımda bağlantı/adres bulunuyor",
            )
        )

    words = count_words(text)
    if words < MIN_SCENE_WORDS:
        issues.append(
            SafetyIssue(
                code="scene_too_short",
                severity=Severity.ERROR,
                language=language,
                scene_id=scene_id,
                message=f"sahne {words} kelime, en az {MIN_SCENE_WORDS} olmalı",
            )
        )
    elif words > MAX_SCENE_WORDS:
        issues.append(
            SafetyIssue(
                code="scene_too_long",
                severity=Severity.WARNING,
                language=language,
                scene_id=scene_id,
                message=f"sahne {words} kelime, {MAX_SCENE_WORDS} kelimeyi aşıyor",
            )
        )

    for sentence in sentences:
        length = count_words(sentence)
        if length > profile.max_sentence_words:
            issues.append(
                SafetyIssue(
                    code="sentence_too_long",
                    severity=Severity.WARNING,
                    language=language,
                    scene_id=scene_id,
                    message=(
                        f"{length} kelimelik cümle (en fazla {profile.max_sentence_words}): "
                        f"“{sentence[:60]}…”"
                    ),
                )
            )

    return issues


def _check_localization(localization, language: str) -> list[SafetyIssue]:
    issues: list[SafetyIssue] = []
    combined = f"{localization.title}\n{localization.description}"

    banned = _find_terms(combined, BANNED_TERMS.get(language, ()))
    if banned:
        issues.append(
            SafetyIssue(
                code="banned_term",
                severity=Severity.ERROR,
                language=language,
                message=f"başlık/açıklamada uygunsuz ifade: {', '.join(banned)}",
            )
        )

    if not localization.tags:
        issues.append(
            SafetyIssue(
                code="missing_tags",
                severity=Severity.WARNING,
                language=language,
                message="etiket listesi boş — keşfedilebilirlik düşer",
            )
        )

    if localization.title.isupper():
        issues.append(
            SafetyIssue(
                code="shouting_title",
                severity=Severity.WARNING,
                language=language,
                message="başlık tamamen büyük harf — clickbait algılanabilir",
            )
        )

    return issues
