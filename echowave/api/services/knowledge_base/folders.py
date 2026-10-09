"""Folders on the Files page, and where each file sits in them.

A file folder **only organises**. It never changes who reads a file: every
agent in the workspace reads every organisation-wide file wherever it sits,
and a channel's or an agent's file is still read only there. That is why
none of the retrieval code looks at ``file_folder_id`` except to say where a
cited passage came from.

"Folder" already means a *channel* in this codebase (``folders``,
``folder_id``). Everything here says *file folder* and touches
``file_folders`` / ``file_folder_id`` only.

Every function takes the caller's ``organization_id`` and resolves every id
it is handed against it, so an id from another workspace is "not found",
never "moved".
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from api.db import db_client

#: How deep folders may nest. Generous for people; a bound for the walks
#: below, which follow parent ids a corrupted row could otherwise loop on.
MAX_DEPTH = 32
MAX_NAME_CHARS = 255
MAX_FILENAME_CHARS = 500

#: What deleting a folder that still holds something does with its contents.
#: There is no default: the person is asked first (see ``FolderNotEmpty``).
MOVE_TO_PARENT = "move_to_parent"
DELETE_CONTENTS = "delete"
DELETE_MODES = (MOVE_TO_PARENT, DELETE_CONTENTS)


class FolderError(Exception):
    """A refusal the person can act on, with the HTTP status it maps to."""

    status_code = 422

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class FolderNotFound(FolderError):
    status_code = 404


class FolderNotEmpty(FolderError):
    """Deleting would take files or folders with it; ask what to do first."""

    status_code = 409

    def __init__(self, files: int, folders: int) -> None:
        parts = []
        if files:
            parts.append(f"{files} file{'s' if files != 1 else ''}")
        if folders:
            parts.append(f"{folders} folder{'s' if folders != 1 else ''}")
        super().__init__(
            f"This folder holds {' and '.join(parts)}. Choose whether to move "
            "them up a level or delete them with it."
        )
        self.files = files
        self.folders = folders


@dataclass
class Tree:
    """The organisation's live folders, indexed for walking."""

    by_id: dict[int, Any]

    def children(self, folder_id: int | None) -> list[Any]:
        return [f for f in self.by_id.values() if f.parent_id == folder_id]

    def descendants(self, folder_id: int) -> list[int]:
        """Every folder below ``folder_id``, not including it."""
        out: list[int] = []
        frontier = [folder_id]
        for _ in range(MAX_DEPTH + 1):
            nxt = [c.id for f in frontier for c in self.children(f)]
            nxt = [i for i in nxt if i not in out and i != folder_id]
            if not nxt:
                break
            out.extend(nxt)
            frontier = nxt
        return out

    def path(self, folder_id: int | None) -> list[Any]:
        """The folders from the top level down to ``folder_id``."""
        chain: list[Any] = []
        current = self.by_id.get(folder_id) if folder_id is not None else None
        while current is not None and len(chain) <= MAX_DEPTH:
            chain.insert(0, current)
            current = (
                self.by_id.get(current.parent_id)
                if current.parent_id is not None
                else None
            )
        return chain

    def path_text(self, folder_id: int | None) -> str:
        """``Pricing/2026``, or ``""`` for the top level."""
        return "/".join(f.name for f in self.path(folder_id))


async def tree(organization_id: int) -> Tree:
    folders = await db_client.list_file_folders(organization_id)
    return Tree(by_id={f.id: f for f in folders})


async def folder_paths(organization_id: int) -> dict[int, str]:
    """Every live folder's path, by id: what a citation names."""
    t = await tree(organization_id)
    return {folder_id: t.path_text(folder_id) for folder_id in t.by_id}


def clean_name(name: Any) -> str:
    """A folder name as it will be stored, or a refusal saying why not."""
    text = " ".join(str(name or "").split())
    if not text:
        raise FolderError("Give the folder a name.")
    if "/" in text or "\\" in text:
        raise FolderError("A folder name cannot contain / or \\.")
    if text in (".", ".."):
        raise FolderError("That name is reserved; choose another.")
    if len(text) > MAX_NAME_CHARS:
        raise FolderError(f"Keep the name under {MAX_NAME_CHARS} characters.")
    return text


def _taken(t: Tree, parent_id: int | None, name: str, exclude: int | None) -> bool:
    return any(
        f.name.lower() == name.lower() and f.id != exclude
        for f in t.children(parent_id)
    )


def _free_name(t: Tree, parent_id: int | None, name: str, exclude: int) -> str:
    """``name``, or ``name (2)``, ``name (3)``... whichever is free there."""
    if not _taken(t, parent_id, name, exclude):
        return name
    for n in range(2, 1000):
        candidate = f"{name} ({n})"
        if not _taken(t, parent_id, candidate, exclude):
            return candidate
    return f"{name} ({exclude})"


async def resolve(organization_id: int, folder_id: int | None) -> int | None:
    """``folder_id`` if it is a live folder of this organisation; None for
    the top level; FolderNotFound otherwise. The check every write of a
    ``file_folder_id`` goes through."""
    if folder_id is None:
        return None
    folder = await db_client.get_file_folder(
        int(folder_id), organization_id=organization_id
    )
    if folder is None:
        raise FolderNotFound("No such folder.")
    return folder.id


def describe(t: Tree, folder: Any, counts: dict[int, int]) -> dict[str, Any]:
    return {
        "id": folder.id,
        "folder_uuid": folder.folder_uuid,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "path": t.path_text(folder.id),
        "file_count": counts.get(folder.id, 0),
        "folder_count": len(t.children(folder.id)),
        "created_at": folder.created_at,
        "updated_at": folder.updated_at,
    }


async def listing(organization_id: int) -> list[dict[str, Any]]:
    """Every live folder, flat, with its path and what it directly holds."""
    t = await tree(organization_id)
    counts = await db_client.file_folder_counts(organization_id)
    return [describe(t, f, counts) for f in t.by_id.values()]


async def create(
    organization_id: int,
    *,
    name: Any,
    parent_id: int | None,
    created_by: int | None,
) -> dict[str, Any]:
    name = clean_name(name)
    parent_id = await resolve(organization_id, parent_id)
    t = await tree(organization_id)
    if len(t.path(parent_id)) >= MAX_DEPTH:
        raise FolderError("Folders cannot nest any deeper here.")
    if _taken(t, parent_id, name, None):
        raise FolderError(
            f"There is already a folder called {name} here.", status_code=409
        )
    folder = await db_client.create_file_folder(
        organization_id=organization_id,
        name=name,
        parent_id=parent_id,
        created_by=created_by,
    )
    t.by_id[folder.id] = folder
    return describe(t, folder, {})


async def ensure_path(
    organization_id: int,
    *,
    parent_id: int | None,
    segments: Iterable[str],
    created_by: int | None,
) -> dict[str, Any] | None:
    """The folder at ``parent_id/segments...``, creating what is missing.

    What a dropped desktop folder uses to keep its structure: the browser
    hands over relative paths (``Contracts/2026/acme.pdf``) and each file is
    uploaded into the folder its path names. Existing folders are reused by
    name, so dropping the same folder twice does not make ``Contracts (2)``.
    Returns None when ``segments`` is empty (``parent_id`` itself).
    """
    current = await resolve(organization_id, parent_id)
    names = [clean_name(s) for s in segments if str(s or "").strip()]
    if not names:
        return None
    t = await tree(organization_id)
    if len(t.path(current)) + len(names) > MAX_DEPTH:
        raise FolderError("That folder nests too deeply to keep its structure.")
    folder: Any = None
    for name in names:
        existing = next(
            (f for f in t.children(current) if f.name.lower() == name.lower()), None
        )
        if existing is None:
            existing = await db_client.create_file_folder(
                organization_id=organization_id,
                name=name,
                parent_id=current,
                created_by=created_by,
            )
            t.by_id[existing.id] = existing
        folder = existing
        current = existing.id
    counts = await db_client.file_folder_counts(organization_id)
    return describe(t, folder, counts)


async def rename(organization_id: int, folder_id: int, name: Any) -> dict[str, Any]:
    name = clean_name(name)
    t = await tree(organization_id)
    folder = t.by_id.get(folder_id)
    if folder is None:
        raise FolderNotFound("No such folder.")
    if _taken(t, folder.parent_id, name, folder.id):
        raise FolderError(
            f"There is already a folder called {name} here.", status_code=409
        )
    updated = await db_client.update_file_folder(
        folder_id, organization_id=organization_id, name=name
    )
    t.by_id[folder_id] = updated
    counts = await db_client.file_folder_counts(organization_id)
    return describe(t, updated, counts)


async def move(
    organization_id: int, folder_id: int, parent_id: int | None
) -> dict[str, Any]:
    """Put a folder inside another (or at the top level, ``None``)."""
    t = await tree(organization_id)
    folder = t.by_id.get(folder_id)
    if folder is None:
        raise FolderNotFound("No such folder.")
    parent_id = await resolve(organization_id, parent_id)
    if parent_id is not None and (
        parent_id == folder_id or parent_id in t.descendants(folder_id)
    ):
        raise FolderError("A folder cannot go inside itself.")
    depth_below = max(
        (len(t.path(d)) - len(t.path(folder_id)) for d in t.descendants(folder_id)),
        default=0,
    )
    if len(t.path(parent_id)) + 1 + depth_below > MAX_DEPTH:
        raise FolderError("Folders cannot nest any deeper there.")
    if _taken(t, parent_id, folder.name, folder.id):
        raise FolderError(
            f"There is already a folder called {folder.name} there.",
            status_code=409,
        )
    updated = await db_client.update_file_folder(
        folder_id, organization_id=organization_id, parent_id=parent_id
    )
    t.by_id[folder_id] = updated
    counts = await db_client.file_folder_counts(organization_id)
    return describe(t, updated, counts)


async def delete(
    organization_id: int, folder_id: int, *, contents: str | None
) -> dict[str, int]:
    """Delete a folder. Empty: just that. Not empty: ``contents`` says what
    happens to what is inside -- ``move_to_parent`` puts its files and
    folders one level up, ``delete`` removes them with it -- and without it
    nothing is deleted and :class:`FolderNotEmpty` says what is there, so
    the screen can ask."""
    t = await tree(organization_id)
    folder = t.by_id.get(folder_id)
    if folder is None:
        raise FolderNotFound("No such folder.")
    if contents is not None and contents not in DELETE_MODES:
        raise FolderError(f"contents must be one of {', '.join(DELETE_MODES)}.")

    children = t.children(folder_id)
    direct_files = await db_client.documents_in_file_folders(
        organization_id, [folder_id]
    )
    if (children or direct_files) and contents is None:
        below = t.descendants(folder_id)
        files_below = await db_client.documents_in_file_folders(
            organization_id, [folder_id, *below]
        )
        raise FolderNotEmpty(files=len(files_below), folders=len(below))

    if contents == DELETE_CONTENTS:
        subtree = [folder_id, *t.descendants(folder_id)]
        files = await db_client.documents_in_file_folders(organization_id, subtree)
        removed = await db_client.archive_documents(
            organization_id, [d.id for d in files]
        )
        await db_client.soft_delete_file_folders(organization_id, subtree)
        return {"files_deleted": removed, "folders_deleted": len(subtree)}

    parent_id = folder.parent_id
    # The folder being deleted is not a neighbour to collide with.
    t.by_id.pop(folder_id, None)
    moved_files = await db_client.move_documents_to_file_folder(
        organization_id, [d.id for d in direct_files], parent_id
    )
    for child in children:
        # A same-named folder already one level up would make two folders
        # nobody can tell apart; the moved one takes the next free name.
        free = _free_name(t, parent_id, child.name, child.id)
        await db_client.update_file_folder(
            child.id,
            organization_id=organization_id,
            parent_id=parent_id,
            name=free,
        )
        child.parent_id = parent_id
        child.name = free
    await db_client.soft_delete_file_folders(organization_id, [folder_id])
    return {
        "files_moved": moved_files,
        "folders_moved": len(children),
        "folders_deleted": 1,
    }


def clean_filename(new_name: Any, current: str) -> str:
    """A file's new name, keeping the ending that says how it is read.

    The reader is chosen by the file's type when it is (re-)read, so a
    rename that drops ``.pdf`` keeps it, and one that changes it to another
    type is refused rather than leaving a PDF called ``notes.txt``.
    """
    text = " ".join(str(new_name or "").split())
    if not text:
        raise FolderError("Give the file a name.")
    if "/" in text or "\\" in text:
        raise FolderError("A file name cannot contain / or \\.")
    current_ext = os.path.splitext(current)[1]
    new_ext = os.path.splitext(text)[1]
    if current_ext and not new_ext:
        text = f"{text}{current_ext}"
    elif current_ext and new_ext.lower() != current_ext.lower():
        raise FolderError(
            f"Keep the {current_ext} ending: the file is read as that type."
        )
    if len(text) > MAX_FILENAME_CHARS:
        raise FolderError(f"Keep the name under {MAX_FILENAME_CHARS} characters.")
    return text


async def place_document(
    organization_id: int,
    document_uuid: str,
    *,
    filename: Any = None,
    file_folder_id: int | None = None,
    move: bool = False,
):
    """Rename a file and/or move it into a folder. Returns the updated row."""
    document = await db_client.get_document_by_uuid(
        document_uuid=document_uuid, organization_id=organization_id
    )
    if document is None:
        raise FolderNotFound("No such file.")
    new_name = (
        clean_filename(filename, document.filename) if filename is not None else None
    )
    target = await resolve(organization_id, file_folder_id) if move else None
    updated = await db_client.update_document_placement(
        document_uuid,
        organization_id=organization_id,
        filename=new_name,
        file_folder_id=target,
        move=move,
    )
    if updated is None:
        raise FolderNotFound("No such file.")
    return updated
