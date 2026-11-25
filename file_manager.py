
"""CloudOps filesystem manager.

Filesystem operations run as the account that starts Flask.
Operating-system permissions remain authoritative.
"""

from __future__ import annotations

import errno
import mimetypes
import os
import platform
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree


IS_WINDOWS = platform.system() == "Windows"
FILESYSTEM_ROOT = Path.home().resolve() if IS_WINDOWS else Path("/")
ALLOWED_STORAGE_ROOTS = {"system": FILESYSTEM_ROOT}

MAX_TEXT_BYTES = 1_048_576
MAX_PREVIEW_BYTES = 15 * 1_024 * 1_024
MAX_DOCX_XML_BYTES = 8 * 1_024 * 1_024


class FileManagerError(ValueError):
    """Invalid, unsafe, or unavailable filesystem operation."""


class FileConflictError(FileManagerError):
    """A destination already exists."""


def get_allowed_storage_roots():
    return [{
        "id": "system",
        "name": "User Files" if IS_WINDOWS else "Linux Filesystem",
        "path": str(FILESYSTEM_ROOT),
    }]


def _root(root_id):
    try:
        return ALLOWED_STORAGE_ROOTS[root_id]
    except KeyError as error:
        raise FileManagerError("Unknown filesystem root.") from error


def _validate_path_text(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise FileManagerError("A valid path is required.")

    if len(value) > 4096:
        raise FileManagerError("Path is too long.")

    return value


def _validate_name(name):
    if not isinstance(name, str) or not name or name in {".", ".."}:
        raise FileManagerError("A valid name is required.")

    if "\x00" in name or "/" in name or "\\" in name or len(name) > 255:
        raise FileManagerError("Invalid filename.")

    if IS_WINDOWS:
        if any(character in name for character in '<>:"|?*') or name.endswith((" ", ".")):
            raise FileManagerError("This filename is not valid on Windows.")

        device_name = name.split(".", 1)[0].upper()
        reserved_names = {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{number}" for number in range(1, 10)),
            *(f"LPT{number}" for number in range(1, 10)),
        }
        if device_name in reserved_names:
            raise FileManagerError("This filename is reserved by Windows.")

    return name


def _has_traversal(path_text):
    return any(
        part == ".."
        for part in path_text.replace("\\", "/").split("/")
    )


def _candidate(root_id, path_value, allow_empty=False):
    if isinstance(path_value, Path):
        path_value = str(path_value)

    if allow_empty and path_value == "":
        path_text = ""
    else:
        path_text = _validate_path_text(path_value)

    if _has_traversal(path_text):
        raise FileManagerError("Path traversal is not permitted.")

    root = _root(root_id)

    candidate = (
        Path(path_text)
        if os.path.isabs(path_text)
        else root / path_text
    )

    candidate = Path(os.path.normpath(str(candidate)))

    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise FileManagerError("Access outside the filesystem root is blocked.") from error

    return candidate


def _ensure_no_symlink(path, allow_missing_final=False):
    """Reject symlinks and Windows junctions rather than following them."""

    current = Path(path.anchor)

    for index, part in enumerate(path.parts[1:]):
        current /= part

        try:
            details = os.lstat(current)

        except FileNotFoundError:
            if allow_missing_final and index == len(path.parts[1:]) - 1:
                return

            raise FileManagerError("The requested path does not exist.")

        except PermissionError as error:
            raise FileManagerError(
                "Your current account cannot access this path."
            ) from error

        junction_check = getattr(current, "is_junction", None)
        if stat.S_ISLNK(details.st_mode) or (
            junction_check is not None and junction_check()
        ):
            raise FileManagerError(
                "Link traversal is disabled. The link itself can be inspected."
            )


def resolve_storage_path(root_id, relative_path=""):
    path = _candidate(root_id, relative_path, allow_empty=True)
    _ensure_no_symlink(path)
    return str(path)


def _existing_path(root_id, path_value):
    path = _candidate(root_id, path_value, allow_empty=True)
    _ensure_no_symlink(path)
    return path


def _require_mutable(path):
    """Protect the configured filesystem root and defer other ACLs to the OS."""

    if path == FILESYSTEM_ROOT:
        raise FileManagerError("The configured storage root cannot be modified.")


def _new_child(root_id, parent_path, name):
    parent = _existing_path(root_id, parent_path)

    if not parent.is_dir():
        raise FileManagerError("The destination parent is not a directory.")

    target = parent / _validate_name(name)

    _ensure_no_symlink(target, allow_missing_final=True)

    return target


def _entry_type(details):
    if stat.S_ISLNK(details.st_mode):
        return "symlink"

    if stat.S_ISDIR(details.st_mode):
        return "directory"

    if stat.S_ISREG(details.st_mode):
        return "file"

    return "other"


def _metadata(path):
    details = os.lstat(path)
    entry_type = _entry_type(details)
    mode = stat.S_IMODE(details.st_mode)

    result = {
        "name": path.name or "/",
        "path": str(path),
        "type": entry_type,
        "size_bytes": (
            details.st_size
            if entry_type in {"file", "symlink"}
            else None
        ),
        "modified": datetime.fromtimestamp(details.st_mtime).isoformat(),
        "created": datetime.fromtimestamp(details.st_ctime).isoformat(),
        "permissions": (
            "Windows ACL" if IS_WINDOWS else stat.filemode(details.st_mode)
        ),
        "mode_octal": None if IS_WINDOWS else format(mode, "03o"),
        "owner_uid": None if IS_WINDOWS else details.st_uid,
        "group_gid": None if IS_WINDOWS else details.st_gid,
        "is_symlink": entry_type == "symlink",
    }

    if entry_type == "symlink":
        result["link_target"] = os.readlink(path)

    return result


def get_directory_listing(root_id="system", relative_path=""):
    directory = _existing_path(root_id, relative_path)

    if not directory.is_dir():
        raise FileManagerError("The requested location is not a directory.")

    entries = []

    try:
        with os.scandir(directory) as items:
            for item in items:
                try:
                    entries.append(_metadata(Path(item.path)))
                except (PermissionError, OSError):
                    continue

    except PermissionError as error:
        raise FileManagerError(
            "Your current account cannot access this directory."
        ) from error

    entries.sort(
        key=lambda entry: (
            entry["type"] not in {"directory", "symlink"},
            entry["name"].lower()
        )
    )

    return {
        "root": root_id,
        "path": str(directory),
        "entries": entries,
        "entry_count": len(entries),
    }


def get_properties(root_id, path_value):
    path = _candidate(root_id, path_value, allow_empty=True)

    if not os.path.lexists(path):
        raise FileManagerError("The requested path does not exist.")

    return _metadata(path)


def _regular_file(root_id, path_value):
    path = _existing_path(root_id, path_value)

    if not path.is_file():
        raise FileManagerError("The requested path is not a regular file.")

    return path


def _open_no_follow(path, flags, mode=0o666):
    nofollow = getattr(os, "O_NOFOLLOW", 0)

    try:
        return os.open(path, flags | nofollow, mode)

    except OSError as error:
        if error.errno in {errno.ELOOP, errno.EACCES}:
            raise FileManagerError(
                "Your operating system denied access to this file."
            ) from error
        raise


def read_text_file(root_id, path_value):
    path = _regular_file(root_id, path_value)

    try:
        fd = _open_no_follow(path, os.O_RDONLY)

        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(MAX_TEXT_BYTES + 1)

    except PermissionError as error:
        raise FileManagerError(
            "Your current account cannot read this file."
        ) from error

    if len(raw) > MAX_TEXT_BYTES:
        raise FileManagerError("Text files are limited to 1 MiB.")

    if b"\x00" in raw:
        raise FileManagerError(
            "Binary files cannot be opened in the text editor."
        )

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FileManagerError(
            "Only UTF-8 text files can be edited here."
        ) from error

    return {
        "path": str(path),
        "content": content,
        "size_bytes": len(raw),
        "mime_type": mimetypes.guess_type(path.name)[0] or "text/plain",
    }


def save_text_file(root_id, path_value, content):
    path = _regular_file(root_id, path_value)
    _require_mutable(path)

    if not isinstance(content, str):
        raise FileManagerError("Text content is required.")

    raw = content.encode("utf-8")

    if len(raw) > MAX_TEXT_BYTES:
        raise FileManagerError("Text content is limited to 1 MiB.")

    try:
        fd = _open_no_follow(
            path,
            os.O_WRONLY | os.O_TRUNC
        )

        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())

    except PermissionError as error:
        raise FileManagerError(
            "Your current account cannot save this file."
        ) from error

    return _metadata(path)


def file_preview_path(root_id, path_value):
    path = _regular_file(root_id, path_value)
    mime_type = mimetypes.guess_type(path.name)[0] or ""

    if path.suffix.casefold() == ".pdf":
        mime_type = "application/pdf"
    elif not mime_type.startswith("image/"):
        raise FileManagerError("Only image and PDF files can be previewed.")

    if os.stat(path).st_size > MAX_PREVIEW_BYTES:
        raise FileManagerError("Previews are limited to 15 MiB.")

    return path, mime_type


def image_preview_path(root_id, path_value):
    path, mime_type = file_preview_path(root_id, path_value)
    if not mime_type.startswith("image/"):
        raise FileManagerError("Only image files can be previewed.")
    return path


def read_docx_preview(root_id, path_value):
    """Extract safe text and tables from a bounded .docx document."""
    path = _regular_file(root_id, path_value)
    if path.suffix.casefold() != ".docx":
        raise FileManagerError("Only .docx documents can be previewed.")

    fd = _open_no_follow(path, os.O_RDONLY)
    with os.fdopen(fd, "rb") as handle:
        size_bytes = os.fstat(handle.fileno()).st_size
        if size_bytes > MAX_PREVIEW_BYTES:
            raise FileManagerError("Document previews are limited to 15 MiB.")

        try:
            with zipfile.ZipFile(handle) as archive:
                document = archive.getinfo("word/document.xml")
                if document.file_size > MAX_DOCX_XML_BYTES:
                    raise FileManagerError(
                        "The expanded document content exceeds the preview limit."
                    )
                xml_content = archive.read(document)
        except FileManagerError:
            raise
        except (
            KeyError,
            OSError,
            RuntimeError,
            zipfile.BadZipFile,
        ) as error:
            raise FileManagerError(
                "This file is not a readable Word document."
            ) from error

    try:
        if (
            b"<!DOCTYPE" in xml_content.upper()
            or b"<!ENTITY" in xml_content.upper()
        ):
            raise FileManagerError(
                "Word document XML cannot contain custom entities."
            )
        root = ElementTree.fromstring(xml_content)
    except FileManagerError:
        raise
    except ElementTree.ParseError as error:
        raise FileManagerError(
            "The Word document contains invalid document content."
        ) from error

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = root.find(f".//{namespace}body")
    if body is None:
        raise FileManagerError("The Word document has no readable body.")

    def paragraph_text(paragraph):
        pieces = []
        for node in paragraph.iter():
            if node.tag == f"{namespace}t":
                pieces.append(node.text or "")
            elif node.tag == f"{namespace}tab":
                pieces.append("\t")
            elif node.tag in {f"{namespace}br", f"{namespace}cr"}:
                pieces.append("\n")
        return "".join(pieces)

    blocks = []
    for block in body:
        if block.tag == f"{namespace}p":
            style = block.find(f"{namespace}pPr/{namespace}pStyle")
            blocks.append({
                "type": "paragraph",
                "style": (
                    style.get(f"{namespace}val", "")
                    if style is not None else ""
                ),
                "text": paragraph_text(block),
            })
        elif block.tag == f"{namespace}tbl":
            rows = []
            for row in block.findall(f"{namespace}tr"):
                rows.append([
                    " ".join(
                        paragraph_text(paragraph)
                        for paragraph in cell.findall(f"{namespace}p")
                    )
                    for cell in row.findall(f"{namespace}tc")
                ])
            blocks.append({"type": "table", "rows": rows})

    return {
        "path": str(path),
        "size_bytes": size_bytes,
        "blocks": blocks,
    }


def download_path(root_id, path_value):
    return _regular_file(root_id, path_value)


def create_file(root_id, parent_path, name, content=""):
    target = _new_child(root_id, parent_path, name)
    _require_mutable(target)

    if os.path.lexists(target):
        raise FileConflictError("A file or folder with that name already exists.")

    if not isinstance(content, str):
        raise FileManagerError("Text content must be a string.")

    raw = content.encode("utf-8")

    if len(raw) > MAX_TEXT_BYTES:
        raise FileManagerError("Text content is limited to 1 MiB.")

    try:
        fd = _open_no_follow(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
        )

        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)

    except PermissionError as error:
        raise FileManagerError(
            "Your current account cannot create this file."
        ) from error

    return _metadata(target)


def create_folder(root_id, parent_path, name):
    target = _new_child(root_id, parent_path, name)
    _require_mutable(target)

    try:
        target.mkdir()

    except FileExistsError as error:
        raise FileConflictError(
            "A file or folder with that name already exists."
        ) from error

    except PermissionError as error:
        raise FileManagerError(
            "Your current account cannot create this folder."
        ) from error

    return _metadata(target)


def _destination(root_id, path_value):
    path = _candidate(root_id, path_value)
    _ensure_no_symlink(path, allow_missing_final=True)
    _require_mutable(path)

    if os.path.lexists(path):
        raise FileConflictError("The destination already exists.")

    parent = path.parent
    _ensure_no_symlink(parent)

    if not parent.is_dir():
        raise FileManagerError("The destination parent is not a directory.")

    return path


def rename_path(root_id, path_value, new_name):
    source = _existing_path(root_id, path_value)
    _require_mutable(source)

    destination = _destination(
        root_id,
        source.parent / _validate_name(new_name)
    )

    try:
        source.rename(destination)

    except PermissionError as error:
        raise FileManagerError(
            "Your current account cannot rename this item."
        ) from error

    return _metadata(destination)


def _check_copy_source(path):
    if path.is_file():
        return

    if not path.is_dir():
        raise FileManagerError(
            "Only regular files and directories can be copied or moved."
        )

    for current, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            child = Path(current) / name
            junction_check = getattr(child, "is_junction", None)
            if os.path.islink(child) or (
                junction_check is not None and junction_check()
            ):
                raise FileManagerError(
                    "Copying directories containing links is disabled."
                )


def copy_path(root_id, source_value, destination_value):
    source = _existing_path(root_id, source_value)
    _require_mutable(source)

    destination = _destination(root_id, destination_value)

    _check_copy_source(source)

    try:
        if source.is_dir():
            shutil.copytree(source, destination, symlinks=False)
        else:
            shutil.copy2(source, destination, follow_symlinks=False)

    except PermissionError as error:
        raise FileManagerError(
            "Your current account cannot copy this item."
        ) from error

    return _metadata(destination)


def move_path(root_id, source_value, destination_value):
    source = _existing_path(root_id, source_value)
    _require_mutable(source)

    destination = _destination(root_id, destination_value)

    _check_copy_source(source)

    try:
        shutil.move(str(source), str(destination))

    except PermissionError as error:
        raise FileManagerError(
            "Your current account cannot move this item."
        ) from error

    return _metadata(destination)


def delete_path(root_id, path_value, confirmation):
    if confirmation != "DELETE":
        raise FileManagerError(
            'Deletion requires the explicit confirmation "DELETE".'
        )

    path = _existing_path(root_id, path_value)
    _require_mutable(path)

    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    except PermissionError as error:
        raise FileManagerError(
            "Your current account cannot delete this item."
        ) from error


def chmod_path(root_id, path_value, mode_value):
    """Change Unix rwx permissions without following symlinks."""

    if IS_WINDOWS:
        raise FileManagerError(
            "Windows file permissions are managed by Windows security settings."
        )

    path = _existing_path(root_id, path_value)
    _require_mutable(path)

    if isinstance(mode_value, int):
        mode = mode_value

    elif isinstance(mode_value, str):
        if not mode_value or len(mode_value) > 3 or not mode_value.isdigit():
            raise FileManagerError("Enter an octal permission such as 755 or 644.")

        if any(character not in "01234567" for character in mode_value):
            raise FileManagerError("Permissions must use octal digits 0–7.")

        mode = int(mode_value, 8)

    else:
        raise FileManagerError("A valid permission mode is required.")

    if mode < 0 or mode > 0o777:
        raise FileManagerError(
            "Only standard read, write and execute permissions (000–777) are supported."
        )

    details = os.lstat(path)

    if not (stat.S_ISREG(details.st_mode) or stat.S_ISDIR(details.st_mode)):
        raise FileManagerError(
            "Permissions can only be changed on regular files and directories."
        )

    try:
        os.chmod(path, mode, follow_symlinks=False)

    except PermissionError as error:
        raise FileManagerError(
            "Your operating system denied this permission change. You must own the file or have administrator privileges."
        ) from error

    except OSError as error:
        raise FileManagerError(
            f"Unable to change permissions: {error}"
        ) from error

    return _metadata(path)


def save_upload(root_id, parent_path, upload):
    if upload is None or not getattr(upload, "filename", ""):
        raise FileManagerError("Choose a file to upload.")

    filename = os.path.basename(upload.filename.replace("\\", "/"))
    target = _new_child(root_id, parent_path, filename)

    _require_mutable(target)

    if os.path.lexists(target):
        raise FileConflictError("A file or folder with that name already exists.")

    try:
        fd = _open_no_follow(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
        )

        with os.fdopen(fd, "wb") as handle:
            shutil.copyfileobj(upload.stream, handle, length=64 * 1024)

    except PermissionError as error:
        raise FileManagerError(
            "Your current account cannot upload to this folder."
        ) from error

    return _metadata(target)
