"""The pages of the window, as a registry: to add a feature, write a `Page` and add one `PageSpec` here. The sidebar,
the order and the lazy building are the shell's job (`ui.shell`); no other file needs to know about the new page."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from e7ac.ui.context import AppContext
from e7ac.ui.pages.base import Page


@dataclass(frozen=True, slots=True)
class PageSpec:
    key: str
    title: str
    build: Callable[[AppContext], Page]
    """Called once, on the first visit (a page's imports and work cost nothing until it is opened)."""
    group: str = ""
    """A heading in the sidebar above this page and the ones after it ("" = none)."""


def _home(context: AppContext) -> Page:
    from e7ac.ui.pages.home import HomePage

    return HomePage(context)


def _arena(context: AppContext) -> Page:
    from e7ac.ui.pages.arena import ArenaPage

    return ArenaPage(context)


def _dev(context: AppContext) -> Page:
    from e7ac.ui.pages.dev import DevPage

    return DevPage(context)


def _settings(context: AppContext) -> Page:
    from e7ac.ui.pages.settings import SettingsPage

    return SettingsPage(context)


PAGES: Final = (
    PageSpec("home", "Home", _home),
    PageSpec("arena", "Arena", _arena),
    PageSpec("dev", "Arena stats", _dev, group="Dev tools"),
    PageSpec("settings", "Settings", _settings),
)
