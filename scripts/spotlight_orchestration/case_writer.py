"""Anchored publication for case-local product files.

Where the platform supports ``dir_fd`` (Linux, macOS) every operation is made
relative to a directory descriptor opened without following symlinks, so a
concurrent rename or symlink swap of the case cannot redirect a read or write.

Windows has no ``dir_fd`` and cannot open a directory with ``os.open``. There
the directory is held by path: every component is checked with ``lstat`` to be
a real directory (no symlink, junction or other name-surrogate reparse point)
and its identity is re-checked before each operation, and every file read is
matched by identity to the entry that was checked. This narrows, but does not
close, the window a concurrent local process has to swap a component; a
descriptor cannot be pinned on Windows from Python.
"""

from __future__ import annotations

import json
import os
import secrets
import stat

from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator, Mapping, Union

from .contract import OrchestrationError

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_NOINHERIT = getattr(os, "O_NOINHERIT", 0)
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW
DESCRIPTOR_ANCHORED = hasattr(os, "O_DIRECTORY") and all(
    function in os.supports_dir_fd for function in (os.open, os.stat, os.unlink, os.mkdir)
)
_NAME_SURROGATE = 0x20000000  # IsReparseTagNameSurrogate: symlinks, junctions, mount points


class NotRegularFile(OSError):
    """A case entry exists but is not a regular file."""


def _is_link(metadata: os.stat_result) -> bool:
    if stat.S_ISLNK(metadata.st_mode):
        return True
    return bool(getattr(metadata, "st_reparse_tag", 0) & _NAME_SURROGATE)


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


class CaseDirectory:
    """One case directory, opened without following links."""

    def lock_exclusive(self) -> None:
        raise NotImplementedError

    def unlock(self) -> None:
        raise NotImplementedError

    def entry_mode(self, name: str) -> int | None:
        """The entry's ``st_mode`` without following links, or None when absent."""
        raise NotImplementedError

    def read_regular(self, name: str) -> bytes | None:
        """A regular file's bytes, None when absent; NotRegularFile otherwise."""
        raise NotImplementedError

    def create_new(self, name: str) -> int:
        """A write descriptor for a new file; FileExistsError when the name is taken."""
        raise NotImplementedError

    def replace(self, source: str, target: str) -> None:
        raise NotImplementedError

    def unlink(self, name: str) -> None:
        raise NotImplementedError

    def mkdir(self, name: str) -> None:
        raise NotImplementedError

    def sync(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class DescriptorDirectory(CaseDirectory):
    def __init__(self, descriptor: int, *, owned: bool = True) -> None:
        self.descriptor = descriptor
        self._owned = owned

    def lock_exclusive(self) -> None:
        import fcntl

        fcntl.flock(self.descriptor, fcntl.LOCK_EX)

    def unlock(self) -> None:
        import fcntl

        fcntl.flock(self.descriptor, fcntl.LOCK_UN)

    def entry_mode(self, name: str) -> int | None:
        try:
            return os.stat(name, dir_fd=self.descriptor, follow_symlinks=False).st_mode
        except FileNotFoundError:
            return None

    def read_regular(self, name: str) -> bytes | None:
        mode = self.entry_mode(name)
        if mode is None:
            return None
        if not stat.S_ISREG(mode):
            raise NotRegularFile(name)
        handle = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=self.descriptor)
        with os.fdopen(handle, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise NotRegularFile(name)
            return stream.read()

    def create_new(self, name: str) -> int:
        return os.open(
            name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | _NOFOLLOW,
            0o600,
            dir_fd=self.descriptor,
        )

    def replace(self, source: str, target: str) -> None:
        os.replace(source, target, src_dir_fd=self.descriptor, dst_dir_fd=self.descriptor)

    def unlink(self, name: str) -> None:
        os.unlink(name, dir_fd=self.descriptor)

    def mkdir(self, name: str) -> None:
        os.mkdir(name, 0o700, dir_fd=self.descriptor)

    def sync(self) -> None:
        os.fsync(self.descriptor)

    def close(self) -> None:
        if self._owned:
            os.close(self.descriptor)


class PathDirectory(CaseDirectory):
    """Windows: a directory held by its checked path chain."""

    LOCK_NAME = ".spotlight-lock"

    def __init__(self, case: Path, parts: tuple[str, ...]) -> None:
        self._chain = [case]
        for part in parts:
            self._chain.append(self._chain[-1] / part)
        self.path = self._chain[-1]
        self._identities = self._walk()
        self._lock: int | None = None

    def _walk(self) -> list[tuple[int, int]]:
        identities = []
        for component in self._chain:
            metadata = os.lstat(component)
            if _is_link(metadata) or not stat.S_ISDIR(metadata.st_mode):
                raise NotADirectoryError(f"not a plain directory: {component}")
            identities.append(_identity(metadata))
        return identities

    def _verify(self) -> Path:
        if self._walk() != self._identities:
            raise NotADirectoryError(f"case directory changed while open: {self.path}")
        return self.path

    def lock_exclusive(self) -> None:
        import msvcrt

        handle = os.open(
            self._verify() / self.LOCK_NAME,
            os.O_RDWR | os.O_CREAT | _BINARY | _NOINHERIT,
            0o600,
        )
        try:
            msvcrt.locking(handle, msvcrt.LK_LOCK, 1)
        except BaseException:
            os.close(handle)
            raise
        self._lock = handle

    def unlock(self) -> None:
        import msvcrt

        if self._lock is None:
            return
        try:
            os.lseek(self._lock, 0, os.SEEK_SET)
            msvcrt.locking(self._lock, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(self._lock)
            self._lock = None

    def entry_mode(self, name: str) -> int | None:
        try:
            metadata = os.lstat(self._verify() / name)
        except FileNotFoundError:
            return None
        return stat.S_IFLNK if _is_link(metadata) else metadata.st_mode

    def read_regular(self, name: str) -> bytes | None:
        path = self._verify() / name
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            return None
        if _is_link(metadata) or not stat.S_ISREG(metadata.st_mode):
            raise NotRegularFile(name)
        handle = os.open(path, os.O_RDONLY | _BINARY | _NOINHERIT)
        with os.fdopen(handle, "rb") as stream:
            if _identity(os.fstat(stream.fileno())) != _identity(metadata):
                raise NotRegularFile(name)
            return stream.read()

    def create_new(self, name: str) -> int:
        return os.open(
            self._verify() / name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | _BINARY | _NOINHERIT,
            0o600,
        )

    def replace(self, source: str, target: str) -> None:
        directory = self._verify()
        os.replace(directory / source, directory / target)

    def unlink(self, name: str) -> None:
        os.unlink(self._verify() / name)

    def mkdir(self, name: str) -> None:
        os.mkdir(self._verify() / name, 0o700)

    def sync(self) -> None:
        # Windows cannot fsync a directory; file contents are fsynced before
        # the rename and NTFS journals the rename itself.
        return None

    def close(self) -> None:
        self.unlock()


DirectoryArgument = Union[CaseDirectory, int]


def as_directory(value: DirectoryArgument) -> CaseDirectory:
    """Accept a borrowed raw descriptor (POSIX pinned callers) or a handle."""
    if isinstance(value, CaseDirectory):
        return value
    return DescriptorDirectory(value, owned=False)


def _relative_parts(relative: str) -> tuple[str, ...]:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise OrchestrationError(f"invalid case-relative path: {relative}")
    return path.parts


def _open(case: Path, parts: tuple[str, ...], anchored: bool) -> CaseDirectory:
    if not anchored:
        return PathDirectory(case, parts)
    descriptor = os.open(case, _DIRECTORY_FLAGS)
    try:
        for part in parts:
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except BaseException:
        os.close(descriptor)
        raise
    return DescriptorDirectory(descriptor)


@contextmanager
def open_case_directory(case: Path, parts: tuple[str, ...]) -> Iterator[CaseDirectory]:
    """Open ``case/parts`` without following links (path-held where dir_fd is missing)."""
    try:
        directory = _open(case, parts, DESCRIPTOR_ANCHORED)
    except OSError as exc:
        raise OrchestrationError("case storage directory is not safely accessible") from exc
    try:
        yield directory
    finally:
        directory.close()


def _existing_bytes(directory: CaseDirectory, name: str) -> bytes | None:
    try:
        return directory.read_regular(name)
    except NotRegularFile as exc:
        raise OrchestrationError(f"refusing to replace non-file case output: {name}") from exc
    except OSError as exc:
        raise OrchestrationError(f"cannot read case output: {name}") from exc


def _stage_bytes(directory: CaseDirectory, name: str, content: bytes) -> str:
    while True:
        temporary_name = f".{name}.{secrets.token_hex(16)}"
        try:
            handle = directory.create_new(temporary_name)
            break
        except FileExistsError:
            continue
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary_name
    except BaseException:
        try:
            directory.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _restore_bytes(directory: CaseDirectory, name: str, content: bytes) -> None:
    temporary_name = _stage_bytes(directory, name, content)
    try:
        directory.replace(temporary_name, name)
    finally:
        try:
            directory.unlink(temporary_name)
        except FileNotFoundError:
            pass


@contextmanager
def _output_directory(
    case: Path,
    parent: tuple[str, ...],
    data_descriptor: DirectoryArgument | None,
) -> Iterator[CaseDirectory]:
    if data_descriptor is not None:
        if parent != ("data",):
            raise OrchestrationError(
                "the locked data descriptor can publish only data-directory outputs"
            )
        yield as_directory(data_descriptor)
        return
    with open_case_directory(case, parent) as directory:
        yield directory


def atomic_write_files(
    case: Path,
    outputs: Mapping[str, bytes],
    *,
    data_descriptor: DirectoryArgument | None = None,
) -> None:
    """Atomically publish same-directory outputs without following symlinks."""
    if not outputs:
        raise OrchestrationError("at least one case output is required")
    parsed = {relative: _relative_parts(relative) for relative in outputs}
    parents = {parts[:-1] for parts in parsed.values()}
    if len(parents) != 1:
        raise OrchestrationError("atomic case outputs must share one directory")
    parent = next(iter(parents))
    with _output_directory(case, parent, data_descriptor) as directory:
        originals = {
            parts[-1]: _existing_bytes(directory, parts[-1]) for parts in parsed.values()
        }
        staged: dict[str, str] = {}
        published: list[str] = []
        try:
            for relative, parts in parsed.items():
                staged[parts[-1]] = _stage_bytes(directory, parts[-1], outputs[relative])
            for name, temporary_name in staged.items():
                directory.replace(temporary_name, name)
                published.append(name)
            directory.sync()
        except BaseException:
            for name in reversed(published):
                original = originals[name]
                if original is None:
                    directory.unlink(name)
                else:
                    _restore_bytes(directory, name, original)
            directory.sync()
            raise
        finally:
            for temporary_name in staged.values():
                try:
                    directory.unlink(temporary_name)
                except FileNotFoundError:
                    pass


@contextmanager
def restore_files_on_error(
    case: Path,
    relatives: tuple[str, ...],
    *,
    data_descriptor: DirectoryArgument | None = None,
) -> Iterator[None]:
    parsed = {relative: _relative_parts(relative) for relative in relatives}
    parents = {parts[:-1] for parts in parsed.values()}
    if len(parents) != 1:
        raise OrchestrationError("restorable case outputs must share one directory")
    parent = next(iter(parents))
    with _output_directory(case, parent, data_descriptor) as directory:
        originals = {
            parts[-1]: _existing_bytes(directory, parts[-1])
            for parts in parsed.values()
        }
        try:
            yield
        except BaseException:
            for name, original in originals.items():
                if original is None:
                    try:
                        directory.unlink(name)
                    except FileNotFoundError:
                        pass
                else:
                    _restore_bytes(directory, name, original)
            directory.sync()
            raise


def atomic_write_json(
    case: Path,
    relative: str,
    value: object,
    *,
    data_descriptor: DirectoryArgument | None = None,
) -> None:
    content = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_files(
        case, {relative: content}, data_descriptor=data_descriptor
    )
