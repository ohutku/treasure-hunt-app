"""AI klip istemlerinin güvenlik süzgeci.

**Neden bu modül var:** Seedance'e karşı Warner Bros. ve Disney'in açtığı
telif ihtarları, modelin mimari düzeyde telifli karakter tasarımları taşıdığı
iddiasına dayanıyor. Çocuklara yönelik bir kanalda üretilen bir karenin
tanınabilir bir çizgi film karakterine benzemesi, olabilecek en kötü
başarısızlık biçimi: hem telif riski hem de kanalın güvenilirliği.

Bu yüzden istemler modele gitmeden önce iki aşamadan geçer:

1. **Reddetme** — marka, franchise veya karakter adı içeren istemler
   gönderilmez. Sessizce temizlemek yerine reddediyoruz: istemi yazan kişi
   ne istediğini bilerek yazmıştır, sessiz bir değişiklik onu yanıltır.
2. **Sıkılaştırma** — kalan isteme özgün eser, karaktersiz, logosuz ve
   yazısız üretim kısıtları eklenir.

Bu bir hukuki güvence değil, makul bir mühendislik önlemi. Üretilen her klip
yine de insan gözüyle kontrol edilmeli — `manifest.<dil>.json` içindeki
`ai_clip_scenes` alanı hangi sahnelerin AI klibi taşıdığını gösterir.
"""

from __future__ import annotations

import re
from functools import lru_cache

#: Tanınabilir fikri mülkiyet göstergeleri. Karakter/franchise/marka adları
#: ve "tarzında üret" kalıpları. Kelime sınırıyla eşleşir.
BLOCKED_TERMS: tuple[str, ...] = (
    # Stüdyolar ve markalar
    "disney", "pixar", "marvel", "dc comics", "warner bros", "nickelodeon",
    "dreamworks", "illumination", "studio ghibli", "ghibli", "netflix",
    # Çocuk içeriğinde en sık karşılaşılan karakterler/franchise'lar
    "mickey mouse", "minnie mouse", "donald duck", "spongebob", "peppa pig",
    "paw patrol", "cocomelon", "bluey", "pokemon", "pikachu", "minions",
    "despicable me", "frozen", "elsa", "anna and elsa", "moana", "encanto",
    "toy story", "buzz lightyear", "woody", "lightning mcqueen", "cars movie",
    "star wars", "baby yoda", "grogu", "darth vader", "jedi", "stormtrooper",
    "spiderman", "spider-man", "batman", "superman", "iron man", "avengers",
    "hello kitty", "sonic the hedgehog", "super mario", "mario bros", "luigi",
    "harry potter", "hogwarts", "shrek", "kung fu panda", "madagascar",
    "thomas the tank engine", "barbie", "lego", "roblox", "minecraft",
    "teletubbies", "sesame street", "elmo", "cookie monster", "winnie the pooh",
    "tom and jerry", "looney tunes", "bugs bunny", "scooby doo", "smurfs",
    "masha and the bear", "gabby's dollhouse", "blippi", "wiggles",
    # "…tarzında" kalıpları — dolaylı IP çağrısı
    "in the style of disney", "in the style of pixar", "in the style of ghibli",
    "disney style", "pixar style", "ghibli style", "anime style of",
)

#: Gerçek kişileri canlandırmayı isteyen kalıplar — çocuk içeriğinde ayrıca riskli.
BLOCKED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:famous|real|actual)\s+(?:person|people|celebrity|actor|actress)\b", "gerçek kişi tasviri"),
    (r"\blooks?\s+like\s+[A-Z][a-z]+\s+[A-Z][a-z]+", "belirli bir kişiye benzetme"),
    (r"\bcopyrighted?\b", "telifli materyal talebi"),
    (r"\btrademarked?\b", "tescilli marka talebi"),
)

#: Her isteme eklenen üretim kısıtları. Modele ne istemediğimizi açıkça söylüyoruz.
HARDENING_SUFFIX = (
    "Original artwork only. No recognizable cartoon characters, no franchise "
    "mascots, no celebrity likenesses, no brand logos, no on-screen text or "
    "watermarks. Gentle camera motion, soft lighting, calm and cheerful mood, "
    "appropriate for young children."
)

#: İstemin azami uzunluğu — çoğu servis uzun istemleri sessizce kırpıyor.
MAX_PROMPT_CHARS = 1200


class ClipPromptRejected(ValueError):
    """İstem, fikri mülkiyet veya uygunluk süzgecine takıldığında."""

    def __init__(self, reason: str, matches: list[str]) -> None:
        self.reason = reason
        self.matches = matches
        detail = ", ".join(matches) if matches else reason
        super().__init__(f"klip istemi reddedildi ({reason}): {detail}")


@lru_cache(maxsize=256)
def _compile_term(term: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)


def find_blocked_terms(prompt: str) -> list[str]:
    """İstemde geçen yasaklı marka/karakter adlarını döndürür."""
    found: list[str] = []
    for term in BLOCKED_TERMS:
        match = _compile_term(term).search(prompt)
        if match:
            found.append(match.group(0))
    return found


def find_blocked_patterns(prompt: str) -> list[str]:
    """Riskli kalıpların açıklamalarını döndürür."""
    return [
        description
        for pattern, description in BLOCKED_PATTERNS
        if re.search(pattern, prompt, re.IGNORECASE)
    ]


def guard_prompt(prompt: str) -> str:
    """İstemi denetler ve üretim kısıtlarıyla sıkılaştırıp döndürür.

    Yasaklı bir ifade bulunursa `ClipPromptRejected` fırlatır — sessizce
    temizlemez, çünkü istemi yazan kişinin bundan haberi olmalı.
    """
    cleaned = " ".join((prompt or "").split())
    if not cleaned:
        raise ClipPromptRejected("boş istem", [])

    blocked = find_blocked_terms(cleaned)
    if blocked:
        raise ClipPromptRejected("tanınabilir fikri mülkiyet", blocked)

    patterns = find_blocked_patterns(cleaned)
    if patterns:
        raise ClipPromptRejected("riskli kalıp", patterns)

    hardened = f"{cleaned.rstrip('.')}. {HARDENING_SUFFIX}"
    if len(hardened) > MAX_PROMPT_CHARS:
        # Kısıtlar korunmalı: kırpma her zaman kullanıcı metninden yapılır.
        room = MAX_PROMPT_CHARS - len(HARDENING_SUFFIX) - 2
        hardened = f"{cleaned[:room].rstrip().rstrip('.')}. {HARDENING_SUFFIX}"
    return hardened
