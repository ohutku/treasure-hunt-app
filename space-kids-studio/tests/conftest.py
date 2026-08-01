"""Ortak test düzenekleri."""

from __future__ import annotations

from pathlib import Path

import pytest

from spacekids.config import PROJECT_ROOT, Settings, VideoSettings
from spacekids.models import Episode, Localization, Motion, Scene, SceneKind, Visual
from spacekids.topics import load_topics


@pytest.fixture(scope="session")
def topics():
    """Depodaki gerçek konu kütüphanesi."""
    return load_topics(PROJECT_ROOT / "config" / "topics.yaml")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Testlere özel, küçük ve hızlı render ayarları."""
    return Settings(
        workspace=tmp_path / "episodes",
        topics_file=PROJECT_ROOT / "config" / "topics.yaml",
        languages=["tr", "en"],
        offline=True,
        # Küçük çözünürlük ve düşük kare hızı testleri saniyeler içinde bitirir.
        video=VideoSettings(width=320, height=180, fps=10, fade_duration=0.2),
    )


def make_scene(
    scene_id: str = "s01",
    *,
    kind: SceneKind = SceneKind.FACT,
    tr: str = "Satürn kocaman bir gezegendir. Etrafında parlak halkaları vardır.",
    en: str = "Saturn is a huge planet. It has bright rings around it.",
    motion: Motion = Motion.ZOOM_IN,
    hero: bool = False,
) -> Scene:
    return Scene(
        id=scene_id,
        kind=kind,
        visual=Visual(
            query=f"space scene {scene_id}",
            motion=motion,
            hero=hero,
            clip_prompt="a friendly cartoon rocket" if hero else None,
        ),
        narration={"tr": tr, "en": en},
    )


def make_localization(language: str = "tr") -> Localization:
    if language == "tr":
        return Localization(
            title="🪐 Satürn'ün Halkaları | Çocuklar İçin Uzay",
            description="Satürn hakkında merak ettiğin her şey.",
            tags=["uzay", "çocuk"],
            hook="Uzaydaki Dev Hulahop!",
        )
    return Localization(
        title="🪐 Saturn's Rings | Space for Kids",
        description="Everything you wondered about Saturn.",
        tags=["space", "kids"],
        hook="The Giant Hula Hoop!",
    )


@pytest.fixture
def episode() -> Episode:
    """Denetimden temiz geçen küçük bir bölüm."""
    return Episode(
        id="ep-test",
        topic_id="saturn-rings",
        languages=["tr", "en"],
        age_min=4,
        age_max=8,
        scenes=[
            make_scene(
                "s01",
                kind=SceneKind.INTRO,
                tr="Merhaba küçük kâşif! Bugün Satürn'ü keşfedeceğiz.",
                en="Hello little explorer! Today we will discover Saturn.",
                hero=True,
            ),
            make_scene("s02"),
            make_scene(
                "s03",
                kind=SceneKind.OUTRO,
                tr="Bugün çok şey öğrendik. Yine görüşmek üzere!",
                en="We learned so much today. See you next time!",
                motion=Motion.PAN_RIGHT,
            ),
        ],
        localizations={"tr": make_localization("tr"), "en": make_localization("en")},
        generator="test",
    )
