"""Bölüm klasörü düzeni.

Her aşama çıktısını diske yazar, böylece:

* araya girip `episode.json`'ı elle düzeltebilirsin,
* pipeline yarıda kalırsa kaldığı yerden devam eder,
* pahalı adımlar (ses, görsel, AI klip) tekrar çalıştırılmaz.

Düzen::

    episodes/ep-001/
    ├── episode.json             # senaryo + yerelleştirmeler
    ├── images/s01.jpg           # diller arasında paylaşılır
    ├── clips/s03.mp4            # AI klipleri (varsa), diller arasında paylaşılır
    ├── audio/tr/s01.mp3
    ├── scenes/tr/s01.mp4        # ara ürün: görsel + ses birleşmiş sahne
    └── out/
        ├── video.tr.mp4
        ├── subtitles.tr.srt
        ├── thumbnail.tr.jpg
        ├── metadata.tr.json
        └── manifest.tr.json
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import Episode, RenderManifest


class EpisodeWorkspace:
    """Tek bir bölümün dosya sistemi üzerindeki temsili."""

    def __init__(self, root: Path, episode_id: str) -> None:
        self.episode_id = episode_id
        self.root = Path(root) / episode_id

    # --- dizinler ---------------------------------------------------------

    @property
    def images_dir(self) -> Path:
        return self.root / "images"

    @property
    def clips_dir(self) -> Path:
        return self.root / "clips"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    def audio_dir(self, language: str) -> Path:
        return self.root / "audio" / language

    def scenes_dir(self, language: str) -> Path:
        return self.root / "scenes" / language

    # --- dosyalar ---------------------------------------------------------

    @property
    def episode_file(self) -> Path:
        return self.root / "episode.json"

    def image_path(self, scene_id: str) -> Path:
        return self.images_dir / f"{scene_id}.jpg"

    def clip_path(self, scene_id: str) -> Path:
        return self.clips_dir / f"{scene_id}.mp4"

    def audio_path(self, scene_id: str, language: str) -> Path:
        return self.audio_dir(language) / f"{scene_id}.mp3"

    def scene_video_path(self, scene_id: str, language: str) -> Path:
        return self.scenes_dir(language) / f"{scene_id}.mp4"

    def video_path(self, language: str) -> Path:
        return self.out_dir / f"video.{language}.mp4"

    def subtitle_path(self, language: str) -> Path:
        return self.out_dir / f"subtitles.{language}.srt"

    def thumbnail_path(self, language: str) -> Path:
        return self.out_dir / f"thumbnail.{language}.jpg"

    def metadata_path(self, language: str) -> Path:
        return self.out_dir / f"metadata.{language}.json"

    def manifest_path(self, language: str) -> Path:
        return self.out_dir / f"manifest.{language}.json"

    # --- işlemler ---------------------------------------------------------

    def prepare(self, languages: list[str]) -> None:
        """Gerekli tüm dizinleri oluşturur."""
        for directory in (self.images_dir, self.clips_dir, self.out_dir):
            directory.mkdir(parents=True, exist_ok=True)
        for language in languages:
            self.audio_dir(language).mkdir(parents=True, exist_ok=True)
            self.scenes_dir(language).mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.episode_file.is_file()

    def save_episode(self, episode: Episode) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        self.episode_file.write_text(
            episode.model_dump_json(indent=2), encoding="utf-8"
        )
        return self.episode_file

    def load_episode(self) -> Episode:
        if not self.episode_file.is_file():
            raise FileNotFoundError(
                f"{self.episode_id} için senaryo bulunamadı. Önce 'spacekids script' çalıştır."
            )
        return Episode.model_validate_json(
            self.episode_file.read_text(encoding="utf-8")
        )

    def save_manifest(self, manifest: RenderManifest) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.manifest_path(manifest.language)
        path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return path

    def save_metadata(self, language: str, metadata: dict[str, object]) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.metadata_path(language)
        path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path


def next_episode_id(root: Path) -> str:
    """Çalışma alanındaki en büyük numaradan bir sonraki bölüm kimliğini üretir."""
    root = Path(root)
    if not root.is_dir():
        return "ep-001"
    numbers = []
    for child in root.iterdir():
        if child.is_dir() and child.name.startswith("ep-"):
            suffix = child.name[3:]
            if suffix.isdigit():
                numbers.append(int(suffix))
    return f"ep-{max(numbers, default=0) + 1:03d}"
