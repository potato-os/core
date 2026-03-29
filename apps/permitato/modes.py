"""Permitato mode definitions — Normal, Work, SFW with Pi-hole group mapping."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModeDefinition:
    name: str
    display_name: str
    group_name: str
    description: str


MODES: dict[str, ModeDefinition] = {
    "normal": ModeDefinition(
        name="normal",
        display_name="Normal",
        group_name="",
        description="No extra restrictions beyond baseline Pi-hole blocking",
    ),
    "work": ModeDefinition(
        name="work",
        display_name="Work",
        group_name="permitato_work",
        description="Social media, entertainment, news, and gaming blocked",
    ),
    "sfw": ModeDefinition(
        name="sfw",
        display_name="SFW",
        group_name="permitato_sfw",
        description="Adult and sexual content blocked",
    ),
}


def get_mode(name: str) -> ModeDefinition:
    """Return mode definition or raise ValueError."""
    if name not in MODES:
        raise ValueError(f"Unknown mode: {name!r}. Valid: {list(MODES)}")
    return MODES[name]


# Domain deny-lists per mode — Pi-hole regex format.
# These get seeded into the matching Pi-hole group during init.

WORK_DENY_DOMAINS: tuple[str, ...] = (
    # Social media
    r"(^|\.)facebook\.com$",
    r"(^|\.)fbcdn\.net$",
    r"(^|\.)instagram\.com$",
    r"(^|\.)twitter\.com$",
    r"(^|\.)x\.com$",
    r"(^|\.)tiktok\.com$",
    r"(^|\.)snapchat\.com$",
    r"(^|\.)reddit\.com$",
    r"(^|\.)threads\.net$",
    r"(^|\.)linkedin\.com$",
    # Entertainment
    r"(^|\.)youtube\.com$",
    r"(^|\.)netflix\.com$",
    r"(^|\.)twitch\.tv$",
    r"(^|\.)disneyplus\.com$",
    r"(^|\.)hulu\.com$",
    r"(^|\.)spotify\.com$",
    # News
    r"(^|\.)news\.ycombinator\.com$",
    r"(^|\.)cnn\.com$",
    r"(^|\.)bbc\.co\.uk$",
    r"(^|\.)foxnews\.com$",
    # Gaming
    r"(^|\.)store\.steampowered\.com$",
    r"(^|\.)epicgames\.com$",
    r"(^|\.)roblox\.com$",
)

SFW_DENY_DOMAINS: tuple[str, ...] = (
    r"(^|\.)pornhub\.com$",
    r"(^|\.)xvideos\.com$",
    r"(^|\.)xnxx\.com$",
    r"(^|\.)xhamster\.com$",
    r"(^|\.)redtube\.com$",
    r"(^|\.)youporn\.com$",
    r"(^|\.)onlyfans\.com$",
    r"(^|\.)chaturbate\.com$",
)
