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

import json
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
    if result.manual_clip_scenes:
        typer.echo(
            f"  📁 Elle konmuş klip kullanılan sahneler: "
            f"{', '.join(result.manual_clip_scenes)}"
        )
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


def _format_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours} sa {minutes} dk"
    return f"{minutes} dk {secs} sn" if minutes else f"{secs} sn"


@app.command("batch")
def batch_run(
    count: int = typer.Option(3, "--count", "-n", min=1, help="Üretilecek bölüm sayısı."),
    topics_option: str | None = typer.Option(
        None, "--topics", help="Virgülle ayrılmış konu kimlikleri (sıra korunur)."
    ),
    languages: str | None = typer.Option(None, help="Virgülle ayrılmış diller."),
    retry: bool = typer.Option(
        False, "--retry", help="Yeni bölüm üretme; yarım kalanları tamamla."
    ),
    offline: bool = typer.Option(False, "--offline", help="Dış servislere çıkma."),
    strict: bool = typer.Option(True, "--strict/--no-strict", help="Denetim hatalarında bölümü atla."),
    workspace: Path | None = typer.Option(None, help="Bölüm klasörlerinin kökü."),
) -> None:
    """Birden fazla bölümü tek komutta üretir.

    Bir bölüm başarısız olursa diğerleri devam eder; özet sonunda raporlanır.
    Yarıda kalan bir çalıştırmayı 'spacekids batch --retry' ile tamamlarsın.
    """
    settings = _settings(offline=offline, languages=languages, workspace=workspace)
    pipeline = Pipeline(settings)
    selected = [lang.strip() for lang in languages.split(",")] if languages else None
    topic_ids = (
        [part.strip() for part in topics_option.split(",") if part.strip()]
        if topics_option
        else None
    )

    if retry:
        pending = pipeline.pending_episodes(selected)
        if not pending:
            typer.secho("✅ Yarım kalan bölüm yok.", fg=typer.colors.GREEN)
            return
        typer.secho(f"🔁 {len(pending)} yarım bölüm tamamlanacak: {', '.join(pending)}\n", bold=True)
    else:
        typer.secho(f"🎬 {count} bölüm üretilecek\n", bold=True)

    def progress(status: str, label: str) -> None:
        if status == "start":
            typer.echo(f"  ▶ {label} …")
        elif status == "ok":
            typer.secho(f"  ✅ {label}", fg=typer.colors.GREEN)
        else:
            typer.secho(f"  ❌ {label}", fg=typer.colors.RED)

    result = pipeline.run_batch(
        count=count,
        topic_ids=topic_ids,
        languages=selected,
        strict=strict,
        retry_pending=retry,
        on_progress=progress,
    )

    if not result.entries:
        typer.secho(
            "\nÜretilecek bölüm bulunamadı. Kütüphanedeki tüm konular kullanılmış "
            "olabilir — config/topics.yaml'a yeni konu ekle.",
            fg=typer.colors.YELLOW,
        )
        return

    typer.secho(f"\n📊 Özet: {result.summary()}", bold=True)
    for entry in result.entries:
        if entry.status == "ok":
            typer.echo(
                f"   {entry.episode_id:8s} {entry.topic_id:16s} "
                f"{_format_duration(entry.video_seconds)} video "
                f"({', '.join(entry.languages)}) — {entry.elapsed:.0f} sn'de"
            )
        else:
            typer.secho(
                f"   {entry.episode_id or '?':8s} {entry.topic_id:16s} HATA: {entry.error}",
                fg=typer.colors.RED,
            )

    typer.echo(
        f"\n   toplam {_format_duration(result.total_video_seconds)} video, "
        f"{_format_duration(result.total_elapsed)} sürede üretildi"
    )

    if result.topics_exhausted:
        typer.secho(
            f"\n⚠️  {result.topics_exhausted} bölüm üretilemedi: kütüphanede yeterli "
            "kullanılmamış konu yok. config/topics.yaml'a yeni konu ekle.",
            fg=typer.colors.YELLOW,
        )

    if result.failed:
        typer.secho(
            "\n   Başarısızları tamamlamak için: spacekids batch --retry",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)


@app.command("providers")
def list_clip_providers() -> None:
    """AI klip sağlayıcılarını ve ücretsiz kullanım notlarını listeler."""
    from .providers.clip_profiles import load_clip_providers

    settings = Settings.load()
    library = load_clip_providers(settings.clip_providers_file)

    typer.secho(f"{len(library.providers)} klip sağlayıcısı:\n", bold=True)
    for profile in library.providers:
        mark = "✅" if profile.verified else "⚠️ "
        typer.secho(f"  {mark} {profile.id:12s} {profile.label}", bold=True)
        if profile.free_tier:
            typer.echo(f"       ücretsiz : {profile.free_tier}")
        if profile.signup_url:
            typer.echo(f"       kayıt    : {profile.signup_url}")
        typer.echo(f"       model    : {profile.default_model}")

    typer.secho(
        "\n⚠️  Hiçbir profil canlı anahtarla doğrulanmadı. Anahtarını aldıktan sonra\n"
        "   önce 'spacekids clips <bölüm> --dry-run' ile ne gönderileceğini gör,\n"
        "   sonra tek klip üretip kontrol et.",
        fg=typer.colors.YELLOW,
    )


@app.command("clips")
def generate_clips(
    episode: str = typer.Argument(..., help="Bölüm kimliği."),
    provider: str | None = typer.Option(None, help="Klip sağlayıcısı (bkz. 'spacekids providers')."),
    model: str | None = typer.Option(None, help="Model kimliğini geçersiz kıl."),
    limit: int | None = typer.Option(None, help="Bu çalıştırmada üretilecek azami klip."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Ağa çıkma, gönderilecek isteği göster."),
    workspace: Path | None = typer.Option(None, help="Bölüm klasörlerinin kökü."),
) -> None:
    """Bölümün yıldız sahneleri için AI video klipleri üretir.

    Klipler render'dan bağımsız üretilebilir: önce burada üretip gözle kontrol
    eder, beğenirsen 'render' ile videoya alırsın.
    """
    from .providers.clip_profiles import load_clip_providers
    from .providers.clip_safety import ClipPromptRejected
    from .providers.clips import ClipProvider as RemoteClipProvider

    settings = _settings(workspace=workspace)
    if provider:
        settings.clips.provider = provider
    if model:
        settings.clips.model = model
    if limit is not None:
        settings.clips.max_clips_per_episode = limit

    space = EpisodeWorkspace(settings.workspace, episode)
    loaded = space.load_episode()
    hero_scenes = loaded.hero_scenes()

    if not hero_scenes:
        typer.secho(
            "Bu bölümde 'hero' işaretli sahne yok — klip üretilecek yer yok.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        library = load_clip_providers(settings.clip_providers_file)
        profile = library.get(settings.clips.provider)
    except (KeyError, FileNotFoundError, ValueError) as error:
        typer.secho(f"❌ {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error

    clip_provider = RemoteClipProvider(settings.clips, profile)

    if dry_run:
        typer.secho(
            f"🔍 Prova çalıştırması — {profile.label} ({profile.id}), hiçbir istek gönderilmiyor\n",
            bold=True,
        )
        for scene in hero_scenes[: settings.clips.max_clips_per_episode]:
            try:
                request = clip_provider.prepare(scene)
            except ClipPromptRejected as error:
                typer.secho(f"  ❌ {scene.id}: {error}", fg=typer.colors.RED)
                continue
            typer.secho(f"  {scene.id} → {request.url}", fg=typer.colors.GREEN)
            typer.echo(f"     model: {request.model}")
            typer.echo(
                "     gövde: "
                + json.dumps(request.body, ensure_ascii=False, indent=6)[:600]
            )
        typer.echo(
            f"\n  Kota: bölüm başına en fazla {settings.clips.max_clips_per_episode} klip, "
            f"{settings.clips.clip_seconds} sn, {settings.clips.resolution}"
        )
        return

    if not clip_provider.is_available():
        typer.secho(
            "❌ Klip üretimi kapalı. Anahtarını ortam değişkenine koy:\n"
            "   export SEEDANCE_API_KEY=...   (veya SPACEKIDS_CLIP_API_KEY)\n"
            f"   Anahtar için: {profile.signup_url or profile.label}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    space.clips_dir.mkdir(parents=True, exist_ok=True)
    produced: list[str] = []
    for scene in hero_scenes:
        typer.echo(f"  {scene.id} üretiliyor…")
        path = clip_provider.generate(scene, space.clip_path(scene.id))
        if path:
            produced.append(scene.id)
            typer.secho(f"    ✅ {path}", fg=typer.colors.GREEN)

    typer.secho(f"\n{len(produced)} klip üretildi.", bold=True)
    for reason in clip_provider.skipped:
        typer.secho(f"  ⚠️  {reason}", fg=typer.colors.YELLOW)
    if produced:
        typer.echo("\nKliplerı gözden geçir, sonra: spacekids render " + episode)


@app.command("upload")
def upload_episode(
    episode: str = typer.Argument(..., help="Bölüm kimliği."),
    languages: str | None = typer.Option(None, help="Yüklenecek diller. Boşsa hepsi."),
    privacy: str = typer.Option(
        "private", help="private | unlisted | public. Varsayılan gizli."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Ağa çıkma, yüklenecekleri göster."),
    client_secrets: Path | None = typer.Option(
        None, help="Google OAuth istemci JSON dosyası (Desktop app)."
    ),
    token_file: Path | None = typer.Option(
        None, help="Kayıtlı OAuth jetonu (varsayılan: ~/.config/spacekids/youtube-token.json)."
    ),
    no_thumbnail: bool = typer.Option(False, "--no-thumbnail", help="Kapak görselini yükleme."),
    no_captions: bool = typer.Option(False, "--no-captions", help="Altyazıyı yükleme."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Gizli olmayan yüklemeyi onayla."),
    workspace: Path | None = typer.Option(None, help="Bölüm klasörlerinin kökü."),
) -> None:
    """Bölümü YouTube'a yükler — her dil ayrı bir video olarak.

    Videolar varsayılan olarak **gizli** yüklenir. YouTube Studio'da izleyip
    onayladıktan sonra yayına almanı öneririm.
    """
    from .publish.youtube import (
        ALLOWED_PRIVACY,
        YouTubeError,
        YouTubeUploader,
        build_upload_plan,
    )

    settings = _settings(workspace=workspace)
    space = EpisodeWorkspace(settings.workspace, episode)
    loaded = space.load_episode()
    targets = (
        [lang.strip() for lang in languages.split(",") if lang.strip()]
        if languages
        else loaded.languages
    )

    if privacy not in ALLOWED_PRIVACY:
        typer.secho(
            f"❌ Geçersiz gizlilik: {privacy!r}. İzin verilenler: {', '.join(ALLOWED_PRIVACY)}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    try:
        plans = [
            build_upload_plan(
                space,
                language,
                privacy=privacy,
                include_thumbnail=not no_thumbnail,
                include_captions=not no_captions,
            )
            for language in targets
        ]
    except YouTubeError as error:
        typer.secho(f"❌ {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error

    if dry_run:
        typer.secho("🔍 Prova çalıştırması — hiçbir şey yüklenmiyor\n", bold=True)
        for plan in plans:
            typer.secho(f"  [{plan.language}] {plan.title}", fg=typer.colors.GREEN)
            typer.echo(f"     video    : {plan.video_path}")
            typer.echo(f"     süre     : {_format_duration(plan.duration_seconds)}")
            typer.echo(f"     gizlilik : {plan.privacy}")
            typer.echo(f"     çocuklar : selfDeclaredMadeForKids={plan.made_for_kids}")
            typer.echo(f"     kategori : {plan.category_id}")
            typer.echo(f"     etiketler: {', '.join(plan.tags)}")
            typer.echo(f"     kapak    : {plan.thumbnail_path or '—'}")
            typer.echo(f"     altyazı  : {plan.caption_path or '—'}")
        return

    if privacy != "private" and not yes:
        typer.secho(
            f"\n⚠️  Videolar '{privacy}' olarak yüklenecek — yani gizli değil.",
            fg=typer.colors.YELLOW,
        )
        if not typer.confirm("Devam edilsin mi?"):
            typer.echo("İptal edildi.")
            raise typer.Exit(code=1)

    token = token_file or (Path.home() / ".config" / "spacekids" / "youtube-token.json")
    uploader = YouTubeUploader(client_secrets_file=client_secrets, token_file=token)

    results = []
    for plan in plans:
        typer.echo(f"\n  [{plan.language}] {plan.title}")

        last_percent = -1

        def progress(percent: int) -> None:
            nonlocal last_percent
            if percent >= last_percent + 10:
                last_percent = percent
                typer.echo(f"     yükleniyor… %{percent}")

        try:
            result = uploader.upload(plan, on_progress=progress)
        except YouTubeError as error:
            typer.secho(f"     ❌ {error}", fg=typer.colors.RED)
            raise typer.Exit(code=1) from error
        except Exception as error:
            typer.secho(f"     ❌ yükleme başarısız: {error}", fg=typer.colors.RED)
            raise typer.Exit(code=1) from error

        results.append(result)
        typer.secho(f"     ✅ {result.url}", fg=typer.colors.GREEN)
        typer.echo(
            f"     kapak: {'✅' if result.thumbnail_set else '—'}  "
            f"altyazı: {'✅' if result.caption_uploaded else '—'}"
        )
        for warning in result.warnings:
            typer.secho(f"     ⚠️  {warning}", fg=typer.colors.YELLOW)

    typer.secho(f"\n✅ {len(results)} video yüklendi.", fg=typer.colors.GREEN, bold=True)
    if any(result.privacy == "private" for result in results):
        typer.echo(
            "   Videolar gizli. YouTube Studio'da izleyip onayladıktan sonra yayına al."
        )


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
