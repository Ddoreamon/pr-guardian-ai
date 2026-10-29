"""Untrusted upload validation. Pure data handling: nothing is imported or executed.

Every rule here is enforced **before** the code is written to disk, and the write
itself is confined to a fresh temporary directory. The module never calls `exec`,
`compile`, `import`, or a package manager on user content.
"""

from __future__ import annotations

import io
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_FILES = 25
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_ZIP_BYTES = 8 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_ANALYSIS_SECONDS = 20.0
MAX_PATH_DEPTH = 6
MAX_NAME_BYTES = 255

_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


class UploadRejected(ValueError):
    """A user-facing rejection with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class UploadedFile:
    path: str          # sanitised relative POSIX path, e.g. "pkg/mod.py"
    content: bytes
    text: str          # decoded UTF-8 source


def _reject(code: str, message: str) -> None:
    raise UploadRejected(code, message)


def _normalise_name(raw: str) -> str:
    """Return a safe relative POSIX path or reject.

    Rejects absolute paths, drive letters, `..`, NUL/control characters, deep nesting
    and Windows reserved names. Unicode is normalised to NFC so that two spellings of
    the same name cannot collide after extraction.
    """
    if not isinstance(raw, str) or not raw.strip():
        _reject("bad_path", "a file name is empty")
    if "\x00" in raw or any(ord(ch) < 32 for ch in raw):
        _reject("bad_path", "a file name contains control characters")
    name = unicodedata.normalize("NFC", raw).replace("\\", "/")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        _reject("absolute_path", f"{raw!r} is an absolute path")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if not parts:
        _reject("bad_path", "a file name is empty")
    if any(part == ".." for part in parts):
        _reject("path_traversal", f"{raw!r} escapes the upload directory")
    if len(parts) > MAX_PATH_DEPTH:
        _reject("too_deep", f"{raw!r} is nested too deeply")
    for part in parts:
        if part.split(".")[0].lower() in _RESERVED:
            _reject("reserved_name", f"{raw!r} uses a reserved device name")
    joined = "/".join(parts)
    if len(joined.encode("utf-8")) > MAX_NAME_BYTES:
        _reject("name_too_long", f"{raw!r} is too long")
    return joined


def _check_extension(path: str) -> None:
    if not path.lower().endswith(".py"):
        _reject("unsupported_type", f"{path!r} is not a .py file")


def _decode(path: str, content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        _reject("bad_encoding", f"{path!r} is not valid UTF-8 ({exc.reason})")


def _check_duplicates(paths: list[str]) -> None:
    seen: dict[str, str] = {}
    for path in paths:
        key = path.casefold()
        if key in seen:
            _reject("duplicate_path",
                    f"{path!r} collides with {seen[key]!r} (case-insensitive)")
        seen[key] = path


def _build(pairs: list[tuple[str, bytes]]) -> list[UploadedFile]:
    if not pairs:
        _reject("empty_upload", "no Python files were uploaded")
    if len(pairs) > MAX_FILES:
        _reject("too_many_files", f"at most {MAX_FILES} files are accepted")
    paths = [_normalise_name(raw) for raw, _ in pairs]
    _check_duplicates(paths)
    total = 0
    files: list[UploadedFile] = []
    for path, (_, content) in zip(paths, pairs, strict=True):
        _check_extension(path)
        if not content:
            _reject("empty_file", f"{path!r} is empty")
        if len(content) > MAX_FILE_BYTES:
            _reject("file_too_large",
                    f"{path!r} is larger than {MAX_FILE_BYTES // 1024} KiB")
        total += len(content)
        if total > MAX_TOTAL_BYTES:
            _reject("upload_too_large",
                    f"the upload exceeds {MAX_TOTAL_BYTES // 1024} KiB in total")
        files.append(UploadedFile(path=path, content=content, text=_decode(path, content)))
    return files


def collect_from_files(pairs: list[tuple[str, bytes]]) -> list[UploadedFile]:
    """Validate a set of (name, bytes) from a multipart request."""
    return _build(pairs)


def collect_from_zip(data: bytes) -> list[UploadedFile]:
    """Validate a ZIP archive without extracting it.

    No file is written here; the caller writes the returned files into a fresh
    temporary directory after every path has been checked.
    """
    if not data:
        _reject("empty_upload", "the archive is empty")
    if len(data) > MAX_ZIP_BYTES:
        _reject("archive_too_large",
                f"the archive is larger than {MAX_ZIP_BYTES // 1024 // 1024} MiB")
    pairs: list[tuple[str, bytes]] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UploadRejected("bad_archive", f"the archive cannot be read: {exc}") from exc

    with archive:
        infos = archive.infolist()
        if not infos:
            _reject("empty_upload", "the archive contains no files")
        if len(infos) > MAX_FILES * 4:
            _reject("too_many_entries", "the archive contains too many entries")

        total_uncompressed = 0
        for info in infos:
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                _reject("symlink_entry",
                        f"{info.filename!r} is a symbolic link, which is not accepted")
            if info.is_dir():
                continue
            path = _normalise_name(info.filename)
            _check_extension(path)
            if info.file_size > MAX_FILE_BYTES:
                _reject("file_too_large",
                        f"{path!r} is larger than {MAX_FILE_BYTES // 1024} KiB")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_TOTAL_BYTES:
                _reject("upload_too_large",
                        f"the archive expands beyond {MAX_TOTAL_BYTES // 1024} KiB")
            if info.compress_size and info.file_size / max(info.compress_size, 1) \
                    > MAX_COMPRESSION_RATIO:
                _reject("compression_ratio",
                        f"{path!r} has an implausible compression ratio")
            if len(pairs) >= MAX_FILES:
                _reject("too_many_files", f"at most {MAX_FILES} files are accepted")
            try:
                content = archive.read(info)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
                raise UploadRejected("bad_archive",
                                     f"{path!r} cannot be extracted: {exc}") from exc
            if len(content) > MAX_FILE_BYTES:
                _reject("file_too_large", f"{path!r} expands past the file limit")
            pairs.append((path, content))

    return _build(pairs)


def safe_target(root, path: str):
    """Join a validated path under `root` and prove the result stays inside it."""
    target = root / PurePosixPath(path)
    resolved_root = root.resolve()
    resolved = target.resolve()
    if resolved_root not in resolved.parents and resolved != resolved_root:
        _reject("path_traversal", f"{path!r} resolves outside the work directory")
    return target
