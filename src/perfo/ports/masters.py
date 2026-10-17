from __future__ import annotations

from abc import ABC, abstractmethod

from perfo.domain.entities import Alias, MasterVersion


class MasterDataPort(ABC):
    @abstractmethod
    def import_master(self, master_type: str, file: str) -> tuple[int | None, list[str]]:
        """Imports a CSV/Excel master. Returns (version, errors); version is None when rejected."""

    @abstractmethod
    def get_master(self, master_type: str, version: int | None = None) -> MasterVersion:
        """Returns the given version or the active one."""

    @abstractmethod
    def add_alias(self, master_type: str, alias_text: str, code: str, user: str) -> Alias: ...

    @abstractmethod
    def get_aliases(self, master_type: str) -> list[Alias]: ...
