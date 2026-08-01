"""Komut satırı arayüzü.

Her aşama ayrı bir komut, çünkü aralara girip düzeltebilmek asıl amaç:
senaryoyu beğenmezsen `episode.json`'ı elle düzenleyip `render` ile devam
edersin.

    spacekids topics                     # konu kütüphanesini listele
    spacekids script --topic mars        # senaryo üret
    spacekids check ep-001               # yaşa uygunluk denetimi
    spacekids render ep-001              # ses + görsel + video
    spacekids run --topic mars           # hepsi bir arada
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from .config import Settings
from .models import SUPPORTED_LANGUAGES
from .pipeline import Pipeline, PipelineError, RenderResult
from .script.builder import TemplateScriptGenerator
from .script.safety import Severity
from .topics import load_topics
from .workspace import EpisodeWorkspace

app = typer.Typer(
    add_completion=False,
    help="Çocuklar için uzay temalı, çok dilli YouTube video üretim hattı.",
)


def _settings(
    offline: bool = False,
    languages: str | None = None,
    workspace: Path | None = None,
) -> Settings:
    overrides: dict[str, object] = {}
    if offline:
        overrides["offline"] = True
    if languages:
        overrides["languages"] = [part.strip() for part in languages.split(",") if part.strip()]
    if workspace:
        overrides["workspace"] = workspace
    return Settings.load(**overrides)


def _pipeline(settings: Settings, use_llm: bool) -> Pipeline:
    if use_llm:
        from .script.llm import LLMScriptGenerator

        generator = LLMScriptGenerator(settings)
    else:
        generator = TemplateScriptGenerator()
    return Pipeline(settings, generator=generator)


def _echo_result(result: RenderResult) -> None:
    typer.secho(f"\n✅ {result.episode.id} hazır", fg=typer.colors.GREEN, bold=True)
    for language, manifest in result.manifests.items():
        typer.echo(f"\n  [{language}] {manifest.duration:.0f} saniye")
        typer.echo(f"    video    : {manifest.video_path}")
        typer.echo(f"    altyazı  : {manifest.subtitle_path}")
        typer.echo(f"    kapak    : {manifest.thumbnail_path}")
        typer.echo(f"    metadata : {manifest.metadata_path}")

    if result.ai_clip_scenes:
        typer.echo(f"\n  🎬 AI klip üretilen sahneler: {', '.join(result.ai_clip_scenes)}")
    if result.image_fallbacks:
        typer.secho(
            f"\n  ⚠️  Şu sahnelerde NASA görseli alınamadı, üretilen görsel kullanıldı: "
            f"{', '.join(result.image_fallbacks)}",
            fg=typer.colors.YELLOW,
        )
    if result.voice_fallbacks:
        typer.secho(
            f"  ⚠️  {result.voice_fallbacks} seslendirme başarısız oldu, sessizliğe düşüldü.",
            fg=typer.colors.YELLOW,
        )


@app.command("topics")
def list_topics(
    topics_file: Path | None = typer.Option(None, help="Alternatif konu dosyası."),
) -> None:
    """Konu kütüphanesini listeler."""
    settings = Settings.load()
    library = load_topics(topics_file or settings.topics_file)
    typer.secho(f"{len(library.topics)} konu:\n", bold=True)
    for topic in library.topics:
        titles = " / ".join(f"{lang}: {topic.title[lang]}" for lang in topic.languages())
        typer.echo(f"  {topic.emoji}  {topic.id:16s} {titles}")
        typer.echo(f"      {len(topic.facts)} bilgi kartı")


@app.command("script")
def make_script(
    topic: str | None = typer.Option(None, help="Konu kimliği. Boşsa kullanılmamış bir konu seçilir."),
    episode: str | None = typer.Option(None, help="Bölüm kimliği (örn. ep-001)."),
    languages: str | None = typer.Option(None, help=f"Virgülle ayrılmış diller. Desteklenen: {', '.join(SUPPORTED_LANGUAGES)}"),
    llm: bool = typer.Option(False, "--llm", help="Şablon yerine Claude ile üret."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Var olan senaryonun üzerine yaz."),
    workspace: Path | None = typer.Option(None, help="Bölüm klasörlerinin kökü."),
) -> None:
    """Bölüm senaryosu üretir ve episode.json olarak kaydeder."""
    settings = _settings(languages=languages, workspace=workspace)
    pipeline = _pipeline(settings, llm)

    selected = pipeline.resolve_topic(topic)
    result = pipeline.create_script(selected, episode, overwrite=overwrite)
    report = pipeline.check(result)

    typer.secho(f"📝 {result.id} — {selected.id} ({result.generator})", bold=True)
    typer.echo(f"   {len(result.scenes)} sahne, diller: {', '.join(result.languages)}")
    typer.echo(f"   dosya: {pipeline.workspace(result.id).episode_file}")
    _echo_report(report)


@app.command("check")
def check_episode_command(
    episode: str = typer.Argument(..., help="Bölüm kimliği."),
    workspace: Path | None = typer.Option(None, help="Bölüm klasörlerinin kökü."),
) -> None:
    """Bölümün yaşa uygunluk denetimini çalıştırır."""
    settings = _settings(workspace=workspace)
    pipeline = Pipeline(settings)
    loaded = pipeline.workspace(episode).load_episode()
    report = pipeline.check(loaded)
    _echo_report(report)
    if not report.ok:
        raise typer.Exit(code=1)


def _echo_report(report) -> None:
    color = typer.colors.GREEN if report.ok else typer.colors.RED
    typer.secho(f"\n🔍 Denetim: {report.summary()}", fg=color, bold=True)
    for issue in report.issues:
        issue_color = (
            typer.colors.RED if issue.severity is Severity.ERROR else typer.colors.YELLOW
        )
        typer.secho(f"   {issue.format()}", fg=issue_color)
    for language, seconds in report.estimated_durations.items():
        typer.echo(f"   tahmini süre [{language}]: {seconds:.0f} sn")


@app.command("render")
def render_episode(
    episode: str = typer.Argument(..., help="Bölüm kimliği."),
    languages: str | None = typer.Option(None, help="Yalnızca bu dilleri render et."),
    offline: bool = typer.Option(False, "--offline", help="Dış servislere çıkma."),
    strict: bool = typer.Option(True, "--strict/--no-strict", help="Denetim hatalarında dur."),
    workspace: Path | None = typer.Option(None, help="Bölüm klasörlerinin kökü."),
) -> None:
    """Var olan bir senaryoyu videoya dönüştürür."""
    settings = _settings(offline=offline, languages=languages, workspace=workspace)
    pipeline = Pipeline(settings)
    loaded = pipeline.workspace(episode).load_episode()
    selected = [lang.strip() for lang in languages.split(",")] if languages else None

    try:
        result = pipeline.render(loaded, languages=selected, strict=strict)
    except PipelineError as error:
        typer.secho(f"\n❌ {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    _echo_result(result)


@app.command("run")
def run_all(
    topic: str | None = typer.Option(None, help="Konu kimliği. Boşsa kullanılmamış bir konu seçilir."),
    episode: str | None = typer.Option(None, help="Bölüm kimliği (örn. ep-001)."),
    languages: str | None = typer.Option(None, help="Virgülle ayrılmış diller."),
    llm: bool = typer.Option(False, "--llm", help="Senaryoyu Claude ile üret."),
    offline: bool = typer.Option(False, "--offline", help="Dış servislere çıkma."),
    strict: bool = typer.Option(True, "--strict/--no-strict", help="Denetim hatalarında dur."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Var olan senaryonun üzerine yaz."),
    workspace: Path | None = typer.Option(None, help="Bölüm klasörlerinin kökü."),
) -> None:
    """Konu seçiminden nihai videoya kadar tüm hattı çalıştırır."""
    settings = _settings(offline=offline, languages=languages, workspace=workspace)
    pipeline = _pipeline(settings, llm)
    selected = [lang.strip() for lang in languages.split(",")] if languages else None

    try:
        result = pipeline.run(
            topic_id=topic,
            episode_id=episode,
            languages=selected,
            strict=strict,
            overwrite=overwrite,
        )
    except PipelineError as error:
        typer.secho(f"\n❌ {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    _echo_result(result)


@app.command("info")
def episode_info(
    episode: str = typer.Argument(..., help="Bölüm kimliği."),
    workspace: Path | None = typer.Option(None, help="Bölüm klasörlerinin kökü."),
) -> None:
    """Bir bölümün mevcut durumunu gösterir."""
    settings = _settings(workspace=workspace)
    space = EpisodeWorkspace(settings.workspace, episode)
    if not space.exists():
        typer.secho(f"❌ {episode} bulunamadı: {space.root}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    loaded = space.load_episode()
    typer.secho(f"📦 {loaded.id} — {loaded.topic_id}", bold=True)
    typer.echo(f"   üreteç : {loaded.generator}")
    typer.echo(f"   sahne  : {len(loaded.scenes)}")
    typer.echo(f"   yaş    : {loaded.age_min}-{loaded.age_max}")
    for language in loaded.languages:
        video = space.video_path(language)
        state = f"{video} ✅" if video.is_file() else "henüz render edilmedi"
        typer.echo(f"   [{language}] {loaded.localizations[language].title}")
        typer.echo(f"        video: {state}")


def main() -> None:  # pragma: no cover - giriş noktası
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
