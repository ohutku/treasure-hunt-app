"""Video montajı.

Her sahne önce kendi başına bir mp4'e render edilir, sonra hepsi birleştirilir.
Bu iki nedenle böyle:

* **Devam edebilirlik** — render yarıda kalırsa tamamlanmış sahneler korunur.
* **Hız** — sahneler aynı kodek ayarlarıyla üretildiği için birleştirme
  yeniden kodlamadan (`-c copy`) yapılır; saniyeler sürer.

Durağan görsellere Ken Burns hareketi (yavaş yakınlaşma/kaydırma) uygulanır;
AI klibi bulunan sahnelerde klip döngüye alınır ve hareket uygulanmaz.
"""

from __future__ import annotations

from pathlib import Path

from ..config import VideoSettings
from ..models import Motion
from .ffmpeg import probe_duration, run

#: Ken Burns hareketinin toplam yakınlaşma oranı.
ZOOM_RANGE = 0.18
#: Kaydırma hareketlerinde uygulanan sabit yakınlaşma (kenar boşluğu yaratır).
PAN_ZOOM = 1.16


def scene_duration(audio_path: Path, settings: VideoSettings) -> float:
    """Sahnenin nihai süresi: anlatım sesi + nefes payı, asgari süreden az olamaz."""
    audio_seconds = probe_duration(audio_path)
    return max(audio_seconds + settings.scene_padding, settings.min_scene_duration)


def _kenburns_filter(motion: Motion, duration: float, settings: VideoSettings) -> str:
    """Ken Burns hareketi için zoompan filtre zinciri kurar.

    Görsel önce çıktıdan büyük bir boyuta ölçekleniyor: zoompan tamsayı piksel
    üzerinden kırptığı için, kaynak çıktıyla aynı boyutta olursa yakınlaşma
    titrek görünür.
    """
    width, height = settings.width, settings.height
    fps = settings.fps
    frames = max(int(round(duration * fps)), 2)
    source = f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,crop={width * 2}:{height * 2}"

    if motion is Motion.ZOOM_IN:
        zoom = f"min(1+{ZOOM_RANGE}*on/{frames},{1 + ZOOM_RANGE})"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif motion is Motion.ZOOM_OUT:
        zoom = f"max({1 + ZOOM_RANGE}-{ZOOM_RANGE}*on/{frames},1)"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif motion is Motion.PAN_LEFT:
        zoom = str(PAN_ZOOM)
        x, y = f"(iw-iw/zoom)*(1-on/{frames})", "ih/2-(ih/zoom/2)"
    else:  # PAN_RIGHT
        zoom = str(PAN_ZOOM)
        x, y = f"(iw-iw/zoom)*on/{frames}", "ih/2-(ih/zoom/2)"

    return (
        f"{source},"
        f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps}"
    )


def _fade_filter(duration: float, settings: VideoSettings) -> str:
    """Sahne başı/sonu karartması — sert geçişleri yumuşatır."""
    fade = settings.fade_duration
    if fade <= 0 or duration <= fade * 2:
        return ""
    return f",fade=t=in:st=0:d={fade:.2f},fade=t=out:st={duration - fade:.2f}:d={fade:.2f}"


def render_scene(
    *,
    destination: Path,
    audio_path: Path,
    duration: float,
    settings: VideoSettings,
    image_path: Path | None = None,
    clip_path: Path | None = None,
    motion: Motion = Motion.ZOOM_IN,
) -> Path:
    """Tek bir sahneyi görsel + ses olarak mp4'e render eder."""
    if image_path is None and clip_path is None:
        raise ValueError("sahne için görsel ya da klip gerekli")

    destination.parent.mkdir(parents=True, exist_ok=True)
    width, height = settings.width, settings.height
    fade = settings.fade_duration

    if clip_path is not None:
        # AI klibi genelde sahneden kısadır; süreyi doldurmak için döngüye alıyoruz.
        inputs = ["-stream_loop", "-1", "-i", str(clip_path)]
        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={settings.fps}"
        )
    else:
        inputs = ["-loop", "1", "-framerate", str(settings.fps), "-i", str(image_path)]
        video_filter = _kenburns_filter(motion, duration, settings)

    video_filter += _fade_filter(duration, settings)
    video_filter += ",format=yuv420p"

    # Anlatım sesi sahneden kısa: kalan süreyi sessizlikle dolduruyoruz.
    audio_filter = f"apad,atrim=0:{duration:.3f},asetpts=N/SR/TB"
    if fade > 0 and duration > fade * 2:
        audio_filter += f",afade=t=out:st={duration - fade:.2f}:d={fade:.2f}"

    run(
        [
            *inputs,
            "-i", str(audio_path),
            "-filter_complex",
            f"[0:v]{video_filter}[v];[1:a]{audio_filter}[a]",
            "-map", "[v]",
            "-map", "[a]",
            "-t", f"{duration:.3f}",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "21",
            "-pix_fmt", "yuv420p",
            "-r", str(settings.fps),
            "-c:a", "aac",
            "-b:a", "160k",
            "-ar", "48000",
            "-ac", "2",
            "-movflags", "+faststart",
            str(destination),
        ],
        description=f"sahne render ({destination.name})",
    )
    return destination


def concat_scenes(scene_paths: list[Path], destination: Path) -> Path:
    """Sahne kliplerini yeniden kodlamadan birleştirir."""
    if not scene_paths:
        raise ValueError("birleştirilecek sahne yok")

    destination.parent.mkdir(parents=True, exist_ok=True)
    listing = destination.parent / f".{destination.stem}-concat.txt"
    listing.write_text(
        "\n".join(f"file '{path.resolve().as_posix()}'" for path in scene_paths) + "\n",
        encoding="utf-8",
    )
    try:
        run(
            [
                "-f", "concat",
                "-safe", "0",
                "-i", str(listing),
                "-c", "copy",
                "-movflags", "+faststart",
                str(destination),
            ],
            description="sahne birleştirme",
        )
    finally:
        listing.unlink(missing_ok=True)
    return destination


def mix_music(video_path: Path, music_path: Path, destination: Path, volume: float) -> Path:
    """Anlatımın altına fon müziği karıştırır.

    Müzik videodan kısaysa döngüye alınır, uzunsa kesilir; anlatım daima
    önde kalır.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-i", str(video_path),
            "-stream_loop", "-1",
            "-i", str(music_path),
            "-filter_complex",
            f"[1:a]volume={volume:.3f}[music];"
            "[0:a][music]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "0:v",
            "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "160k",
            "-movflags", "+faststart",
            str(destination),
        ],
        description="müzik karıştırma",
    )
    return destination
