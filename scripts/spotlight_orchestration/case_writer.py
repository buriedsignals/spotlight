"""Descriptor-anchored publication for case-local product files."""

from __future__ import annotations

import json
import os
import secrets
import stat

from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator, Mapping

from .contract import OrchestrationError

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW


def _relative_parts(relative: str) -> tuple[str, ...]:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise OrchestrationError(f"invalid case-relative path: {relative}")
    return path.parts


@contextmanager
def open_case_directory(case: Path, parts: tuple[str, ...]) -> Iterator[int]:
    descriptor: int | None = None
    try:
        descriptor = os.open(case, _DIRECTORY_FLAGS)
        for part in parts:
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    except OSError as exc:
        raise OrchestrationError("case storage directory is not safely accessible") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _existing_bytes(descriptor: int, name: str) -> bytes | None:
    try:
        metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OrchestrationError(f"cannot inspect case output: {name}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise OrchestrationError(f"refusing to replace non-file case output: {name}")
    try:
        handle = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=descriptor)
        with os.fdopen(handle, "rb") as stream:
            return stream.read()
    except OSError as exc:
        raise OrchestrationError(f"cannot read case output: {name}") from exc


def _stage_bytes(descriptor: int, name: str, content: bytes) -> str:
    while True:
        temporary_name = f".{name}.{secrets.token_hex(16)}"
        try:
            handle = os.open(
                temporary_name,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | _NOFOLLOW,
                0o600,
                dir_fd=descriptor,
            )
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
            os.unlink(temporary_name, dir_fd=descriptor)
        except FileNotFoundError:
            pass
        raise


def _restore_bytes(descriptor: int, name: str, content: bytes) -> None:
    temporary_name = _stage_bytes(descriptor, name, content)
    try:
        os.replace(temporary_name, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
    finally:
        try:
            os.unlink(temporary_name, dir_fd=descriptor)
        except FileNotFoundError:
            pass


@contextmanager
def _output_directory(
    case: Path,
    parent: tuple[str, ...],
    data_descriptor: int | None,
) -> Iterator[int]:
    if data_descriptor is not None:
        if parent != ("data",):
            raise OrchestrationError(
                "the locked data descriptor can publish only data-directory outputs"
            )
        yield data_descriptor
        return
    with open_case_directory(case, parent) as descriptor:
        yield descriptor


def atomic_write_files(
    case: Path,
    outputs: Mapping[str, bytes],
    *,
    data_descriptor: int | None = None,
) -> None:
    """Atomically publish same-directory outputs without following symlinks."""
    if not outputs:
        raise OrchestrationError("at least one case output is required")
    parsed = {relative: _relative_parts(relative) for relative in outputs}
    parents = {parts[:-1] for parts in parsed.values()}
    if len(parents) != 1:
        raise OrchestrationError("atomic case outputs must share one directory")
    parent = next(iter(parents))
    with _output_directory(case, parent, data_descriptor) as descriptor:
        originals = {
            parts[-1]: _existing_bytes(descriptor, parts[-1]) for parts in parsed.values()
        }
        staged: dict[str, str] = {}
        published: list[str] = []
        try:
            for relative, parts in parsed.items():
                staged[parts[-1]] = _stage_bytes(
                    descriptor, parts[-1], outputs[relative]
                )
            for name, temporary_name in staged.items():
                os.replace(
                    temporary_name,
                    name,
                    src_dir_fd=descriptor,
                    dst_dir_fd=descriptor,
                )
                published.append(name)
            os.fsync(descriptor)
        except BaseException:
            for name in reversed(published):
                original = originals[name]
                if original is None:
                    os.unlink(name, dir_fd=descriptor)
                else:
                    _restore_bytes(descriptor, name, original)
            os.fsync(descriptor)
            raise
        finally:
            for temporary_name in staged.values():
                try:
                    os.unlink(temporary_name, dir_fd=descriptor)
                except FileNotFoundError:
                    pass


@contextmanager
def restore_files_on_error(
    case: Path,
    relatives: tuple[str, ...],
    *,
    data_descriptor: int | None = None,
) -> Iterator[None]:
    parsed = {relative: _relative_parts(relative) for relative in relatives}
    parents = {parts[:-1] for parts in parsed.values()}
    if len(parents) != 1:
        raise OrchestrationError("restorable case outputs must share one directory")
    parent = next(iter(parents))
    with _output_directory(case, parent, data_descriptor) as descriptor:
        originals = {
            parts[-1]: _existing_bytes(descriptor, parts[-1])
            for parts in parsed.values()
        }
        try:
            yield
        except BaseException:
            for name, original in originals.items():
                if original is None:
                    try:
                        os.unlink(name, dir_fd=descriptor)
                    except FileNotFoundError:
                        pass
                else:
                    _restore_bytes(descriptor, name, original)
            os.fsync(descriptor)
            raise


def atomic_write_json(
    case: Path,
    relative: str,
    value: object,
    *,
    data_descriptor: int | None = None,
) -> None:
    content = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_files(
        case, {relative: content}, data_descriptor=data_descriptor
    )
