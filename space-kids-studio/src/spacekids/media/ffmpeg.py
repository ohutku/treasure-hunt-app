"""ffmpeg sarmalayıcısı.

Sistemde ffmpeg kurulu olmayabilir; bu yüzden önce `imageio-ffmpeg` paketiyle
gelen statik binary'yi kullanıyoruz. Kurulum adımı, indirme, PATH ayarı yok —
`pip install` yeter.

Süre ölçümü de buradan yapılıyor: `ffprobe` her ortamda bulunmadığı için
süreyi ffmpeg'in kendi çıktısından okuyoruz.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

#: ffmpeg ilerleme satırlarındaki "time=00:00:12.34" biçimi.
_TIME_PATTERN = re.compile(r"time=(\d+):(\d{2}):(\d{2}\.?\d*)")
_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}\.?\d*)")


class FFmpegError(RuntimeError):
    """ffmpeg sıfırdan farklı bir çıkış kodu döndürdüğünde fırlatılır."""


@lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    """Kullanılacak ffmpeg çalıştırılabilirinin yolu.

    Sıralama: paketle gelen statik binary → sistemdeki ffmpeg.
    """
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        system = shutil.which("ffmpeg")
        if system:
            return system
        raise FFmpegError(
            "ffmpeg bulunamadı. 'pip install imageio-ffmpeg' çalıştır veya "
            "sistemine ffmpeg kur."
        ) from None


def run(args: list[str], *, description: str = "ffmpeg") -> str:
    """ffmpeg'i çalıştırır ve stderr çıktısını döndürür."""
    command = [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", *args]
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0:
        tail = "\n".join(process.stderr.strip().splitlines()[-15:])
        raise FFmpegError(f"{description} başarısız (kod {process.returncode}):\n{tail}")
    return process.stderr


def probe_duration(path: Path) -> float:
    """Bir medya dosyasının süresini saniye olarak döndürür.

    ffprobe her kurulumda bulunmadığı için dosyayı ffmpeg ile null çıkışa
    çözüp son zaman damgasını okuyoruz. Yavaş bir yöntem değil: veri diske
    yazılmıyor.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"süre ölçülemedi, dosya yok: {path}")

    stderr = run(["-i", str(path), "-f", "null", "-"], description=f"süre ölçümü ({path.name})")

    matches = _TIME_PATTERN.findall(stderr)
    if matches:
        hours, minutes, seconds = matches[-1]
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    header = _DURATION_PATTERN.search(stderr)
    if header:
        hours, minutes, seconds = header.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    raise FFmpegError(f"{path.name}: süre bilgisi okunamadı")


def render_silence(destination: Path, duration: float, sample_rate: int = 24000) -> Path:
    """Verilen uzunlukta sessiz bir mp3 üretir."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-f", "lavfi",
            "-i", f"anullsrc=channel_layout=mono:sample_rate={sample_rate}",
            "-t", f"{max(duration, 0.1):.3f}",
            "-c:a", "libmp3lame",
            "-q:a", "9",
            str(destination),
        ],
        description="sessizlik üretimi",
    )
    return destination
