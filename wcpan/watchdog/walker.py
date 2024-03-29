__all__ = ("Change", "ChangeEntry", "ChangeSet", "Walker")


import enum
import os
from typing import TypeAlias

from .filters import Filter


class Change(enum.IntEnum):
    added = 1
    modified = 2
    deleted = 3


ChangeEntry: TypeAlias = tuple[Change, str]
ChangeSet: TypeAlias = set[ChangeEntry]
Snapshot: TypeAlias = dict[str, float]


class Walker(object):
    def __init__(self, filter_: Filter, root_path: str):
        self._files: Snapshot = {}
        self._filter = filter_
        self._root_path = root_path

    def _walk(
        self,
        dir_path: str,
        changes: ChangeSet,
        new_files: Snapshot,
    ) -> None:
        with os.scandir(dir_path) as scanner:
            for entry in scanner:
                if not self._filter(entry):
                    continue

                if entry.is_dir():
                    self._walk(entry.path, changes, new_files)
                    continue

                mtime = entry.stat().st_mtime
                new_files[entry.path] = mtime
                old_mtime = self._files.get(entry.path)
                if not old_mtime:
                    changes.add((Change.added, entry.path))
                elif old_mtime != mtime:
                    changes.add((Change.modified, entry.path))

    def __call__(self) -> ChangeSet:
        changes: set[ChangeEntry] = set()
        new_files: Snapshot = {}
        try:
            self._walk(self._root_path, changes, new_files)
        except OSError as e:
            # happens when a directory has been deleted between checks
            print(f"error walking file system: {e.__class__.__name__} {e}")

        # look for deleted
        deleted = self._files.keys() - new_files.keys()
        if deleted:
            deleted_set: set[ChangeEntry] = {(Change.deleted, path) for path in deleted}
            changes |= deleted_set

        self._files = new_files
        return changes
