"""What every page of the window shares: the app's folders, the one watch (so that the Arena and Dev pages never run
two at once), the catalog's heroes and the hero portraits."""

from __future__ import annotations

from e7ac.appdata import catalog_heroes, open_engine
from e7ac.arena.references import ReferenceStore
from e7ac.arena.sightings import SightingStore, popularity, sightings_path
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.crashlog import PreviousRun
from e7ac.paths import AppPaths
from e7ac.roster.store import list_owned
from e7ac.storage.db import session_scope
from e7ac.ui.art import Portraits, hero_of_the_day
from e7ac.ui.watch_controller import WatchController


class AppContext:
    def __init__(self, paths: AppPaths, watch: WatchController, previous: PreviousRun | None = None) -> None:
        self.paths = paths
        self.watch = watch
        self.previous = previous
        """The run before this one did not end normally (Home says so until it is dismissed)."""
        self.portraits = Portraits(paths.cache_dir)
        self._heroes: dict[str, ResolvedEntity] | None = None

    def heroes(self, reload: bool = False) -> dict[str, ResolvedEntity]:
        """The catalog's heroes by code (empty when there is no catalog yet)."""
        if self._heroes is None or reload:
            with session_scope(open_engine(self.paths)) as session:
                self._heroes = catalog_heroes(session) or {}
        return self._heroes

    def refresh_icon(self) -> None:
        """The program's icon again (the portrait it is made from was just downloaded, or the user chose a picture)."""
        from PySide6.QtWidgets import QApplication

        from e7ac.ui import icon

        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setWindowIcon(icon.app_icon(self.paths))

    def hero_name(self, code: str) -> str:
        hero = self.heroes().get(code)
        return hero.name if hero is not None and hero.name else code

    def featured(self, count: int) -> list[str]:
        """Heroes to decorate with, those whose portrait is cached: your last Arena team, the heroes met most in Arena,
        then your roster (up to `count`)."""
        chosen: list[str] = []

        def add(codes: list[str]) -> None:
            for code in self.portraits.cached(codes):
                if code not in chosen and len(chosen) < count:
                    chosen.append(code)

        add(ReferenceStore(self.paths.home / "references" / "arena").last_team())
        sightings, _ = SightingStore(sightings_path(self.paths.home)).read()
        add([h.code for h in popularity(sightings).heroes])
        if len(chosen) < count and self.paths.database.is_file():
            with session_scope(open_engine(self.paths)) as session:
                add([row.hero_code for row, _ in list_owned(session)])
        return chosen

    def hero_of_the_day(self) -> str | None:
        return hero_of_the_day(self.featured(12))
