"""Üretim hattının orkestratörü.

Aşamalar sırayla çalışır ve her biri çıktısını diske yazar:

    plan → script → check → render (görsel · ses · sahne · birleştirme) → publish

Her adım **idempotent**: çıktısı zaten varsa yeniden üretmez. Yarıda kalan bir
render, aynı komutla kaldığı yerden devam eder; pahalı adımlar (AI klip,
seslendirme) tekrarlanmaz.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .media.subtitles import write_srt
from .media.video import concat_scenes, mix_music, render_scene, scene_duration
from .models import Episode, RenderManifest, SceneTiming
from .providers.base import ClipProvider, ImageProvider, VoiceProvider
from .providers.images import (
    FallbackImageProvider,
    NasaImageProvider,
    ProceduralImageProvider,
)
from .providers.clips import ClipProvider as RemoteClipProvider
from .providers.clips import NullClipProvider
from .providers.nasa_video import NasaVideoProvider
from .providers.voice import EdgeVoiceProvider, FallbackVoiceProvider, SilentVoiceProvider
from .publish.metadata import build_youtube_metadata
from .publish.thumbnail import build_thumbnail
from .script.builder import ScriptGenerator, TemplateScriptGenerator
from .script.safety import SafetyReport, check_episode
from .topics import Topic, load_topics
from .workspace import EpisodeWorkspace, next_episode_id

logger = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    """Pipeline devam edemediğinde fırlatılır."""


@dataclass
class RenderResult:
    """Bir bölümün render sonucu."""

    episode: Episode
    manifests: dict[str, RenderManifest] = field(default_factory=dict)
    #: Görsel için prosedürel üretime düşülen sahneler.
    image_fallbacks: list[str] = field(default_factory=list)
    #: Seslendirmenin sessizliğe düştüğü durum sayısı.
    voice_fallbacks: int = 0
    #: AI klip üretilen sahneler.
    ai_clip_scenes: list[str] = field(default_factory=list)
    #: Elle konmuş klibin kullanıldığı sahneler (clips/<sahne>.mp4).
    manual_clip_scenes: list[str] = field(default_factory=list)


@dataclass
class BatchEntry:
    """Toplu üretimde tek bir bölümün sonucu."""

    episode_id: str
    topic_id: str
    status: str  # "ok" | "failed"
    elapsed: float
    languages: list[str] = field(default_factory=list)
    #: Üretilen videonun toplam süresi (tüm diller birlikte).
    video_seconds: float = 0.0
    error: str | None = None


@dataclass
class BatchResult:
    """Toplu üretimin özeti."""

    entries: list[BatchEntry] = field(default_factory=list)
    #: Kütüphanede yeterli kullanılmamış konu kalmadıysa, eksik kalan sayı.
    topics_exhausted: int = 0

    @property
    def succeeded(self) -> list[BatchEntry]:
        return [entry for entry in self.entries if entry.status == "ok"]

    @property
    def failed(self) -> list[BatchEntry]:
        return [entry for entry in self.entries if entry.status == "failed"]

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def total_video_seconds(self) -> float:
        return sum(entry.video_seconds for entry in self.entries)

    @property
    def total_elapsed(self) -> float:
        return sum(entry.elapsed for entry in self.entries)

    def summary(self) -> str:
        return f"{len(self.succeeded)} başarılı, {len(self.failed)} başarısız"


def build_providers(
    settings: Settings,
) -> tuple[ImageProvider, VoiceProvider, ClipProvider]:
    """Ayarlara göre sağlayıcıları kurar.

    `--offline` modda hiçbir dış servise çıkılmaz: görseller prosedürel
    üretilir, sesler sessiz olur. Çevrimiçi modda gerçek sağlayıcılar denenir
    ve başarısız olurlarsa aynı yerel üretime düşülür — yani ağ sorunu
    pipeline'ı asla durdurmaz.
    """
    procedural = ProceduralImageProvider()
    silent = SilentVoiceProvider()

    if settings.offline:
        return procedural, silent, NullClipProvider()

    images = FallbackImageProvider(NasaImageProvider(), procedural)
    voices = FallbackVoiceProvider(EdgeVoiceProvider(settings.voices), silent)

    return images, voices, build_clip_provider(settings)


def build_clip_provider(settings: Settings) -> ClipProvider:
    """Klip kaynağını ayarlara göre seçer.

    Yanlış yapılandırma bölümü durdurmaz: klip kullanılamıyorsa sahneler
    durağan görselle render edilir, çünkü klip daima isteğe bağlıdır.
    """
    if settings.offline or settings.clips.source == "none":
        return NullClipProvider()

    if settings.clips.source == "nasa":
        return NasaVideoProvider(max_bytes=settings.clips.max_bytes)

    if settings.clips.source == "ai":
        if not (settings.clips.enabled and settings.clips.api_key):
            logger.warning("AI klip kaynağı seçili ama anahtar yok; klip üretilmeyecek.")
            return NullClipProvider()
        try:
            return RemoteClipProvider.from_settings(
                settings.clips, settings.clip_providers_file
            )
        except (KeyError, FileNotFoundError, ValueError) as error:
            logger.warning("AI klip sağlayıcısı kurulamadı, devre dışı: %s", error)
            return NullClipProvider()

    logger.warning(
        "Bilinmeyen klip kaynağı %r; klip üretilmeyecek.", settings.clips.source
    )
    return NullClipProvider()


class Pipeline:
    """Bölüm üretim hattı."""

    def __init__(
        self,
        settings: Settings,
        *,
        generator: ScriptGenerator | None = None,
        image_provider: ImageProvider | None = None,
        voice_provider: VoiceProvider | None = None,
        clip_provider: ClipProvider | None = None,
    ) -> None:
        self.settings = settings
        self.generator = generator or TemplateScriptGenerator()

        defaults = build_providers(settings)
        self.image_provider = image_provider or defaults[0]
        self.voice_provider = voice_provider or defaults[1]
        self.clip_provider = clip_provider or defaults[2]

    # --- aşamalar --------------------------------------------------------

    def workspace(self, episode_id: str) -> EpisodeWorkspace:
        return EpisodeWorkspace(self.settings.workspace, episode_id)

    def resolve_topic(self, topic_id: str | None) -> Topic:
        library = load_topics(self.settings.topics_file)
        if topic_id:
            return library.get(topic_id)
        used = self._used_topic_ids()
        return library.pick(exclude=used)

    def _used_topic_ids(self) -> set[str]:
        """Daha önce üretilmiş bölümlerin konuları — tekrarı önlemek için."""
        root = Path(self.settings.workspace)
        if not root.is_dir():
            return set()
        used: set[str] = set()
        for child in sorted(root.iterdir()):
            workspace = EpisodeWorkspace(root, child.name)
            if workspace.exists():
                try:
                    used.add(workspace.load_episode().topic_id)
                except Exception:  # bozuk bölüm dosyası seçim akışını durdurmasın
                    continue
        return used

    def create_script(
        self, topic: Topic, episode_id: str | None = None, *, overwrite: bool = False
    ) -> Episode:
        """Senaryoyu üretir ve diske yazar."""
        episode_id = episode_id or next_episode_id(self.settings.workspace)
        workspace = self.workspace(episode_id)

        if workspace.exists() and not overwrite:
            return workspace.load_episode()

        episode = self.generator.generate(
            topic,
            episode_id=episode_id,
            languages=self.settings.languages,
            age_min=self.settings.age_min,
            age_max=self.settings.age_max,
            fact_scene_count=self.settings.fact_scene_count,
        )
        workspace.save_episode(episode)
        return episode

    def check(self, episode: Episode) -> SafetyReport:
        return check_episode(episode)

    def render(
        self,
        episode: Episode,
        *,
        languages: list[str] | None = None,
        strict: bool = True,
    ) -> RenderResult:
        """Bölümü videoya dönüştürür."""
        report = self.check(episode)
        if strict and not report.ok:
            details = "\n".join(f"  - {issue.format()}" for issue in report.errors)
            raise PipelineError(
                f"Güvenlik denetimi başarısız ({report.summary()}):\n{details}\n"
                "Senaryoyu düzelt ya da --no-strict ile zorla."
            )

        languages = languages or episode.languages
        unknown = [lang for lang in languages if lang not in episode.languages]
        if unknown:
            raise PipelineError(
                f"Bölümde bulunmayan dil(ler): {', '.join(unknown)}. "
                f"Mevcut: {', '.join(episode.languages)}"
            )

        workspace = self.workspace(episode.id)
        workspace.prepare(languages)
        result = RenderResult(episode=episode)

        # 1) Görseller ve AI klipleri diller arasında paylaşılır — bir kez üretilir.
        images: dict[str, Path] = {}
        clips: dict[str, Path] = {}
        for scene in episode.scenes:
            images[scene.id] = self.image_provider.fetch(
                scene, workspace.image_path(scene.id)
            )

            # Elle konmuş klip her şeyin önünde gelir: kullanıcı klibi başka bir
            # araçla üretip `clips/<sahne>.mp4` olarak bırakabilir. Bu, klip
            # üretimini API'ye bağımlı olmaktan çıkarır ve `hero` işareti de
            # gerektirmez — dosya varsa niyet bellidir.
            clip_path = workspace.clip_path(scene.id)
            if clip_path.is_file() and clip_path.stat().st_size > 0:
                clips[scene.id] = clip_path
                result.manual_clip_scenes.append(scene.id)
                continue

            clip = self.clip_provider.generate(scene, clip_path)
            if clip is not None:
                clips[scene.id] = clip
                result.ai_clip_scenes.append(scene.id)

        # 2) Her dil için ses, sahne klipleri ve nihai video.
        for language in languages:
            result.manifests[language] = self._render_language(
                episode, language, workspace, images, clips
            )

        result.image_fallbacks = list(
            getattr(self.image_provider, "fallback_scenes", [])
        )
        result.voice_fallbacks = int(getattr(self.voice_provider, "fallback_count", 0))
        return result

    def _render_language(
        self,
        episode: Episode,
        language: str,
        workspace: EpisodeWorkspace,
        images: dict[str, Path],
        clips: dict[str, Path],
    ) -> RenderManifest:
        video_settings = self.settings.video
        timings: list[SceneTiming] = []
        scene_videos: list[Path] = []
        cursor = 0.0

        for scene in episode.scenes:
            audio = self.voice_provider.synthesize(
                scene.text(language), language, workspace.audio_path(scene.id, language)
            )
            duration = scene_duration(audio, video_settings)
            scene_video = workspace.scene_video_path(scene.id, language)

            if not scene_video.is_file():
                render_scene(
                    destination=scene_video,
                    audio_path=audio,
                    duration=duration,
                    settings=video_settings,
                    image_path=images.get(scene.id),
                    clip_path=clips.get(scene.id),
                    motion=scene.visual.motion,
                )

            timings.append(
                SceneTiming(scene_id=scene.id, start=cursor, duration=duration)
            )
            cursor += duration
            scene_videos.append(scene_video)

        video_path = workspace.video_path(language)
        video_path.unlink(missing_ok=True)
        concat_scenes(scene_videos, video_path)

        music = video_settings.music_path
        if music and Path(music).is_file():
            mixed = video_path.with_suffix(".music.mp4")
            mix_music(video_path, Path(music), mixed, video_settings.music_volume)
            mixed.replace(video_path)

        subtitle_path = write_srt(
            episode, language, timings, workspace.subtitle_path(language)
        )

        # Kapak için ilk hero sahnesini, yoksa ikinci sahneyi kullan.
        hero_scenes = episode.hero_scenes()
        cover_scene = hero_scenes[0] if hero_scenes else episode.scenes[min(1, len(episode.scenes) - 1)]
        thumbnail_path = build_thumbnail(
            images[cover_scene.id],
            episode.localizations[language].hook,
            workspace.thumbnail_path(language),
        )

        metadata = build_youtube_metadata(
            episode, language, timings, duration=cursor
        )
        metadata_path = workspace.save_metadata(language, metadata)

        manifest = RenderManifest(
            episode_id=episode.id,
            language=language,
            video_path=str(video_path),
            subtitle_path=str(subtitle_path),
            thumbnail_path=str(thumbnail_path),
            metadata_path=str(metadata_path),
            duration=cursor,
            timings=timings,
            ai_clip_scenes=sorted(clips),
        )
        workspace.save_manifest(manifest)
        return manifest

    # --- uçtan uca -------------------------------------------------------

    def run(
        self,
        *,
        topic_id: str | None = None,
        episode_id: str | None = None,
        languages: list[str] | None = None,
        strict: bool = True,
        overwrite: bool = False,
    ) -> RenderResult:
        """Konu seçiminden nihai videoya kadar tüm hattı çalıştırır."""
        topic = self.resolve_topic(topic_id)
        episode = self.create_script(topic, episode_id, overwrite=overwrite)
        return self.render(episode, languages=languages, strict=strict)

    # --- toplu üretim ----------------------------------------------------

    def pending_episodes(self, languages: list[str] | None = None) -> list[str]:
        """Senaryosu olan ama videosu tamamlanmamış bölümleri listeler.

        Gece boyu süren bir toplu üretimde bir bölüm hata alırsa, sonraki
        çalıştırmada baştan başlamak yerine yalnızca eksikleri tamamlamak
        isteriz.
        """
        root = Path(self.settings.workspace)
        if not root.is_dir():
            return []

        pending: list[str] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            workspace = EpisodeWorkspace(root, child.name)
            if not workspace.exists():
                continue
            try:
                episode = workspace.load_episode()
            except Exception:
                continue
            targets = languages or episode.languages
            if any(not workspace.video_path(lang).is_file() for lang in targets):
                pending.append(child.name)
        return pending

    def select_batch_topics(self, count: int, topic_ids: list[str] | None = None) -> list[Topic]:
        """Toplu üretim için konu seçer.

        Açık liste verilmediyse **kütüphane sırasına göre** kullanılmamış
        konular seçilir. Sıra rastgele değil: aynı komutu tekrar çalıştırdığında
        ne olacağını tahmin edebilmek, gece çalışan bir işte rastgelelikten
        daha değerli.
        """
        library = load_topics(self.settings.topics_file)
        if topic_ids:
            return [library.get(topic_id) for topic_id in topic_ids]

        used = self._used_topic_ids()
        available = [topic for topic in library.topics if topic.id not in used]
        return available[:count]

    def run_batch(
        self,
        *,
        count: int = 1,
        topic_ids: list[str] | None = None,
        languages: list[str] | None = None,
        strict: bool = True,
        retry_pending: bool = False,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> BatchResult:
        """Birden fazla bölümü sırayla üretir.

        **Hata toleranslı**: bir bölüm başarısız olursa diğerleri devam eder ve
        hata özette raporlanır. Beş bölümlük bir gece işinin üçüncüde durup
        kalması, tek bir bozuk konudan çok daha pahalıya mal olur.
        """
        result = BatchResult()

        if retry_pending:
            targets: list[tuple[str, str | None]] = [
                (episode_id, None) for episode_id in self.pending_episodes(languages)
            ]
        else:
            topics = self.select_batch_topics(count, topic_ids)
            if not topic_ids and len(topics) < count:
                result.topics_exhausted = count - len(topics)
            targets = [(None, topic.id) for topic in topics]

        for episode_id, topic_id in targets:
            started = time.monotonic()
            label = episode_id or topic_id or "?"
            if on_progress:
                on_progress("start", label)

            entry = BatchEntry(
                episode_id=episode_id or "",
                topic_id=topic_id or "",
                status="failed",
                elapsed=0.0,
            )
            try:
                if episode_id:
                    episode = self.workspace(episode_id).load_episode()
                else:
                    topic = self.select_batch_topics(1, [topic_id])[0]
                    episode = self.create_script(topic)

                entry.episode_id = episode.id
                entry.topic_id = episode.topic_id

                render = self.render(episode, languages=languages, strict=strict)
                entry.status = "ok"
                entry.languages = sorted(render.manifests)
                entry.video_seconds = sum(
                    manifest.duration for manifest in render.manifests.values()
                )
            except Exception as error:
                entry.error = str(error)
                logger.warning("Bölüm üretilemedi (%s): %s", label, error)

            entry.elapsed = time.monotonic() - started
            result.entries.append(entry)
            if on_progress:
                on_progress(entry.status, entry.episode_id or label)

        return result
