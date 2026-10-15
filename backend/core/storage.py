"""Blob storage for receipt photos (fin-02).

``StorageBackend`` is the whole contract (``put`` / ``get`` / ``delete`` by key, plus
``delete_prefix`` to drop a user's or an expense's whole folder); the only implementation today is a
folder on local disk. Keys are built by the SERVER from ids
(``u/{user_id}/{expense_id}/{attachment_id}.{ext}``) -- never from anything a client sent -- and the
backend accepts NOTHING else: not a similar-looking name, not another extension. On top of the
shape check it still resolves the path and refuses anything that lands outside its root.

Nothing here logs a key, a path or an exception message: those carry user and expense ids.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

from core.config import settings

logger = logging.getLogger("kapital.storage")

# str(uuid.uuid4()): lowercase hex, 8-4-4-4-12. The only shape the server ever produces.
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_KEY = re.compile(rf"u/{_UUID}/{_UUID}/{_UUID}\.(?:jpg|png|webp)")
# What may be removed as a whole: one user's folder, or one expense's folder inside it.
_PREFIX = re.compile(rf"u/{_UUID}(?:/{_UUID})?")


class InvalidStorageKey(ValueError):
    """The key is not one the server could have built (or would leave the storage root)."""


@runtime_checkable
class StorageBackend(Protocol):
    def put(self, key: str, data: bytes | bytearray) -> None:
        """Store ``data`` under ``key`` (replacing what was there)."""

    def get(self, key: str) -> bytes:
        """The bytes under ``key``; ``FileNotFoundError`` when there are none."""

    def delete(self, key: str) -> None:
        """Remove ``key``; a missing key is not an error."""

    def delete_prefix(self, prefix: str) -> None:
        """Remove a user's (``u/{user}``) or an expense's (``u/{user}/{expense}``) whole folder,
        whatever is in it -- stray and half-written files included. Missing is not an error."""


class LocalFsStorage:
    """Files under one root folder, written atomically (temp file + rename)."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root).resolve()

    def _inside_root(self, pattern: re.Pattern[str], name: str) -> Path:
        if not isinstance(name, str) or not pattern.fullmatch(name):
            raise InvalidStorageKey("invalid storage key")
        lexical = self._root / name
        path = lexical.resolve()
        # resolve() follows symlinks: the result must still sit under the root, and it must be
        # exactly where the name says -- a symlink on the way (even one that stays inside the
        # root, say u/<user> -> u/<other user>) would otherwise send an operation to a stranger's files.
        if path == self._root or not path.is_relative_to(self._root) or path != lexical:
            raise InvalidStorageKey("invalid storage key")
        return path

    def _path(self, key: str) -> Path:
        return self._inside_root(_KEY, key)

    def put(self, key: str, data: bytes | bytearray) -> None:
        path = self._path(key)
        try:
            self._write_atomic(path, data)
        except FileNotFoundError:
            # A concurrent delete pruned the (then empty) folder between mkdir and write.
            self._write_atomic(path, data)

    @staticmethod
    def _write_atomic(path: Path, data: bytes | bytearray) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)
        self._prune_empty_folders(path.parent)

    def delete_prefix(self, prefix: str) -> None:
        folder = self._inside_root(_PREFIX, prefix)
        if folder.is_dir():
            shutil.rmtree(folder)  # unlinks symlinks inside; never follows them
        self._prune_empty_folders(folder.parent)

    def _prune_empty_folders(self, folder: Path) -> None:
        """Remove now-empty ``u/{user}/{expense}`` folders, never the root itself."""
        while folder != self._root and self._root in folder.parents:
            try:
                folder.rmdir()
            except OSError:  # not empty (or in use): leave it
                return
            folder = folder.parent


def get_storage() -> StorageBackend:
    """The configured backend (a FastAPI dependency; tests override or re-point UPLOADS_DIR)."""
    return LocalFsStorage(settings.uploads_dir)


def _log_failures(what: str, total: int, failures: list[str]) -> None:
    # The line names the kind of error only -- never a key, a path or the error text, all of
    # which would carry user or expense ids.
    if failures:
        logger.warning(
            "attachment %s failed (%d of %d): %s",
            what,
            len(failures),
            total,
            ", ".join(sorted(set(failures))),
        )


def remove_files(storage: StorageBackend, keys: Iterable[str]) -> None:
    """Best-effort removal AFTER the database commit: a file that will not go is a log line."""
    failures: list[str] = []
    total = 0
    for key in keys:
        total += 1
        try:
            storage.delete(key)
        except Exception as exc:  # noqa: BLE001 - any failure here must not break the request
            failures.append(type(exc).__name__)
    _log_failures("file removal", total, failures)


def remove_tree(storage: StorageBackend, prefix: str) -> None:
    """Best-effort removal of a whole folder AFTER the database commit (account deletion)."""
    try:
        storage.delete_prefix(prefix)
    except Exception as exc:  # noqa: BLE001 - see remove_files
        _log_failures("folder removal", 1, [type(exc).__name__])
