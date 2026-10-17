"""What every page of the window shares: the app's folders, the one watch (so that the Arena and Dev pages never run
two at once) and the catalog's heroes."""

from __future__ import annotations

from dataclasses import dataclass, field

from e7ac.appdata import catalog_heroes, open_engine
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.paths import AppPaths
from e7ac.storage.db import session_scope
from e7ac.ui.watch_controller import WatchController


@dataclass(slots=True)
class AppContext:
    paths: AppPaths
    watch: WatchController
    _heroes: dict[str, ResolvedEntity] | None = field(default=None, repr=False)

    def heroes(self, reload: bool = False) -> dict[str, ResolvedEntity]:
        """The catalog's heroes by code (empty when there is no catalog yet)."""
        if self._heroes is None or reload:
            with session_scope(open_engine(self.paths)) as session:
                self._heroes = catalog_heroes(session) or {}
        return self._heroes

    def hero_name(self, code: str) -> str:
        hero = self.heroes().get(code)
        return hero.name if hero is not None and hero.name else code
