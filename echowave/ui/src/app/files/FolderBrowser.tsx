'use client';

/**
 * Where you are on the Files page, and the folders here: breadcrumbs from
 * the top, a tile per folder, and "New folder". Every crumb and tile takes a
 * drop -- a file row dragged from the list moves there, files dragged from
 * the desktop upload there.
 *
 * File folders only organise. Nothing here changes who reads a file.
 */

import { ChevronRight, Folder, FolderPlus, MoreHorizontal } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

import {
    createFileFolderApiV1KnowledgeBaseFileFoldersPost,
    deleteFileFolderApiV1KnowledgeBaseFileFoldersFolderIdDelete,
    updateFileFolderApiV1KnowledgeBaseFileFoldersFolderIdPatch,
} from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { detailFromError } from '@/lib/apiError';
import { cn } from '@/lib/utils';

import { breadcrumbs, carriesDesktopFiles, carriesFileRow, childrenOf, FILE_DRAG_TYPE, type FileFolder, subtree } from './fileFolders';

/** A place something can be dropped: a folder, or the top level (null). */
export type DropTarget = number | null;

type Props = {
    folders: readonly FileFolder[];
    currentId: number | null;
    onOpen: (folderId: number | null) => void;
    /** Folders changed (made, renamed, moved, deleted): read them again. */
    onChanged: () => void;
    /** Desktop files dropped on a crumb or a tile. */
    onDropFiles: (target: DropTarget, data: DataTransfer) => void;
    /** A file row from the list dropped on a crumb or a tile. */
    onMoveFile: (documentUuid: string, target: DropTarget) => void;
};

type Naming = { mode: 'create' } | { mode: 'rename'; folder: FileFolder };

export function FolderBrowser({ folders, currentId, onOpen, onChanged, onDropFiles, onMoveFile }: Props) {
    const [naming, setNaming] = useState<Naming | null>(null);
    const [name, setName] = useState('');
    const [nameError, setNameError] = useState<string | null>(null);
    const [moving, setMoving] = useState<FileFolder | null>(null);
    const [deleting, setDeleting] = useState<FileFolder | null>(null);
    const [busy, setBusy] = useState(false);
    const [over, setOver] = useState<string | null>(null);

    const trail = breadcrumbs(folders, currentId);
    const here = childrenOf(folders, currentId);

    /** Drop handlers for one target. `key` only marks which one is lit. */
    const dropZone = (key: string, target: DropTarget) => ({
        onDragOver: (event: React.DragEvent) => {
            if (!carriesFileRow(event.dataTransfer) && !carriesDesktopFiles(event.dataTransfer)) return;
            event.preventDefault();
            event.stopPropagation();
            setOver(key);
        },
        onDragLeave: () => setOver((was) => (was === key ? null : was)),
        onDrop: (event: React.DragEvent) => {
            if (carriesFileRow(event.dataTransfer)) {
                event.preventDefault();
                event.stopPropagation();
                setOver(null);
                const uuid = event.dataTransfer.getData(FILE_DRAG_TYPE);
                if (uuid) onMoveFile(uuid, target);
                return;
            }
            if (carriesDesktopFiles(event.dataTransfer)) {
                event.preventDefault();
                event.stopPropagation();
                setOver(null);
                onDropFiles(target, event.dataTransfer);
            }
        },
    });

    const openNaming = (next: Naming) => {
        setNaming(next);
        setName(next.mode === 'rename' ? next.folder.name : '');
        setNameError(null);
    };

    const saveName = async () => {
        if (!naming) return;
        setBusy(true);
        const response =
            naming.mode === 'create'
                ? await createFileFolderApiV1KnowledgeBaseFileFoldersPost({ body: { name, parent_id: currentId } })
                : await updateFileFolderApiV1KnowledgeBaseFileFoldersFolderIdPatch({
                      path: { folder_id: naming.folder.id },
                      body: { name },
                  });
        setBusy(false);
        if (response.error) {
            setNameError(detailFromError(response.error, 'Could not save the folder'));
            return;
        }
        setNaming(null);
        onChanged();
    };

    const moveFolder = async (folder: FileFolder, parentId: number | null) => {
        setBusy(true);
        const response = await updateFileFolderApiV1KnowledgeBaseFileFoldersFolderIdPatch({
            path: { folder_id: folder.id },
            body: { parent_id: parentId },
        });
        setBusy(false);
        if (response.error) {
            toast.error(detailFromError(response.error, 'Could not move the folder'));
            return;
        }
        setMoving(null);
        toast.success(`Moved ${folder.name}`);
        onChanged();
    };

    const deleteFolder = async (folder: FileFolder, contents: 'move_to_parent' | 'delete' | null) => {
        setBusy(true);
        const response = await deleteFileFolderApiV1KnowledgeBaseFileFoldersFolderIdDelete({
            path: { folder_id: folder.id },
            query: contents ? { contents } : {},
        });
        setBusy(false);
        if (response.error) {
            toast.error(detailFromError(response.error, 'Could not delete the folder'));
            return;
        }
        setDeleting(null);
        toast.success(`Deleted ${folder.name}`);
        if (currentId != null && subtree(folders, folder.id).has(currentId)) onOpen(folder.parent_id ?? null);
        onChanged();
    };

    const holds = (folder: FileFolder) => (folder.file_count ?? 0) + (folder.folder_count ?? 0);
    const parentName = (folder: FileFolder) =>
        folder.parent_id == null ? 'the top level' : folders.find((f) => f.id === folder.parent_id)?.name ?? 'the folder above';

    return (
        <div className="space-y-3" data-testid="folder-browser">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <nav aria-label="Folder path" className="flex min-w-0 flex-wrap items-center gap-1 text-sm">
                    <button
                        type="button"
                        onClick={() => onOpen(null)}
                        aria-current={currentId == null ? 'page' : undefined}
                        className={cn(
                            'rounded px-1.5 py-0.5 hover:bg-muted',
                            currentId == null && 'font-medium',
                            over === 'crumb-top' && 'bg-primary/10 ring-1 ring-primary',
                        )}
                        {...dropZone('crumb-top', null)}
                    >
                        All files
                    </button>
                    {trail.map((folder, index) => (
                        <span key={folder.id} className="flex items-center gap-1">
                            <ChevronRight aria-hidden className="h-3.5 w-3.5 text-muted-foreground" />
                            <button
                                type="button"
                                onClick={() => onOpen(folder.id)}
                                aria-current={index === trail.length - 1 ? 'page' : undefined}
                                className={cn(
                                    'max-w-48 truncate rounded px-1.5 py-0.5 hover:bg-muted',
                                    index === trail.length - 1 && 'font-medium',
                                    over === `crumb-${folder.id}` && 'bg-primary/10 ring-1 ring-primary',
                                )}
                                {...dropZone(`crumb-${folder.id}`, folder.id)}
                            >
                                {folder.name}
                            </button>
                        </span>
                    ))}
                </nav>
                <Button variant="outline" size="sm" onClick={() => openNaming({ mode: 'create' })}>
                    <FolderPlus className="mr-2 h-4 w-4" />
                    New folder
                </Button>
            </div>

            {here.length > 0 && (
                <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3" aria-label="Folders">
                    {here.map((folder) => (
                        <li
                            key={folder.id}
                            data-testid={`folder-${folder.id}`}
                            className={cn(
                                'flex items-center gap-2 rounded-lg border p-2 pl-3 hover:bg-muted/50',
                                over === `tile-${folder.id}` && 'border-primary bg-primary/10',
                            )}
                            {...dropZone(`tile-${folder.id}`, folder.id)}
                        >
                            <button
                                type="button"
                                className="flex min-w-0 flex-1 items-center gap-2 text-left"
                                onClick={() => onOpen(folder.id)}
                            >
                                <Folder aria-hidden className="h-5 w-5 shrink-0 text-primary" />
                                <span className="min-w-0">
                                    <span className="block truncate font-medium">{folder.name}</span>
                                    <span className="block text-xs text-muted-foreground">
                                        {holds(folder) === 0
                                            ? 'Empty'
                                            : [
                                                  folder.file_count ? `${folder.file_count} file${folder.file_count === 1 ? '' : 's'}` : null,
                                                  folder.folder_count ? `${folder.folder_count} folder${folder.folder_count === 1 ? '' : 's'}` : null,
                                              ]
                                                  .filter(Boolean)
                                                  .join(', ')}
                                    </span>
                                </span>
                            </button>
                            <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <Button variant="ghost" size="sm" aria-label={`More for ${folder.name}`}>
                                        <MoreHorizontal className="h-4 w-4" />
                                    </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end">
                                    <DropdownMenuItem onSelect={() => openNaming({ mode: 'rename', folder })}>Rename</DropdownMenuItem>
                                    <DropdownMenuItem onSelect={() => setMoving(folder)}>Move to…</DropdownMenuItem>
                                    <DropdownMenuItem className="text-destructive" onSelect={() => setDeleting(folder)}>
                                        Delete
                                    </DropdownMenuItem>
                                </DropdownMenuContent>
                            </DropdownMenu>
                        </li>
                    ))}
                </ul>
            )}

            <Dialog open={naming != null} onOpenChange={(open) => !open && setNaming(null)}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>{naming?.mode === 'rename' ? 'Rename folder' : 'New folder'}</DialogTitle>
                        <DialogDescription>
                            Folders only organise your files. Every agent still reads every file here.
                        </DialogDescription>
                    </DialogHeader>
                    <form
                        onSubmit={(event) => {
                            event.preventDefault();
                            void saveName();
                        }}
                        className="space-y-2"
                    >
                        <Input
                            autoFocus
                            aria-label="Folder name"
                            value={name}
                            onChange={(event) => setName(event.target.value)}
                            placeholder="e.g. Price lists"
                        />
                        {nameError && <p className="text-sm text-destructive">{nameError}</p>}
                        <DialogFooter>
                            <Button type="button" variant="ghost" onClick={() => setNaming(null)}>
                                Cancel
                            </Button>
                            <Button type="submit" disabled={busy || !name.trim()}>
                                {naming?.mode === 'rename' ? 'Rename' : 'Create folder'}
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>

            <Dialog open={moving != null} onOpenChange={(open) => !open && setMoving(null)}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Move {moving?.name}</DialogTitle>
                        <DialogDescription>Choose where it goes. Everything inside it moves with it.</DialogDescription>
                    </DialogHeader>
                    {moving && (
                        <FolderPicker
                            folders={folders}
                            exclude={subtree(folders, moving.id)}
                            current={moving.parent_id ?? null}
                            disabled={busy}
                            onPick={(target) => void moveFolder(moving, target)}
                        />
                    )}
                </DialogContent>
            </Dialog>

            <Dialog open={deleting != null} onOpenChange={(open) => !open && setDeleting(null)}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Delete {deleting?.name}?</DialogTitle>
                        <DialogDescription>
                            {deleting && holds(deleting) > 0
                                ? `It holds ${[
                                      deleting.file_count ? `${deleting.file_count} file${deleting.file_count === 1 ? '' : 's'}` : null,
                                      deleting.folder_count ? `${deleting.folder_count} folder${deleting.folder_count === 1 ? '' : 's'}` : null,
                                  ]
                                      .filter(Boolean)
                                      .join(' and ')}. Move them to ${parentName(deleting)}, or delete them with it? Deleted files cannot be brought back, and agents stop answering from them.`
                                : 'The folder is empty.'}
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter className="gap-2 sm:gap-0">
                        <Button variant="ghost" onClick={() => setDeleting(null)}>
                            Cancel
                        </Button>
                        {deleting && holds(deleting) > 0 ? (
                            <>
                                <Button variant="outline" disabled={busy} onClick={() => void deleteFolder(deleting, 'move_to_parent')}>
                                    Move them, delete the folder
                                </Button>
                                <Button variant="destructive" disabled={busy} onClick={() => void deleteFolder(deleting, 'delete')}>
                                    Delete everything
                                </Button>
                            </>
                        ) : (
                            deleting && (
                                <Button variant="destructive" disabled={busy} onClick={() => void deleteFolder(deleting, null)}>
                                    Delete folder
                                </Button>
                            )
                        )}
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

/** Every place something can go, as paths: the top level, then each folder. */
export function FolderPicker({
    folders,
    exclude,
    current,
    disabled,
    onPick,
}: {
    folders: readonly FileFolder[];
    exclude?: Set<number>;
    current: number | null;
    disabled?: boolean;
    onPick: (target: number | null) => void;
}) {
    const options = [...folders]
        .filter((f) => !exclude?.has(f.id))
        .sort((a, b) => a.path.localeCompare(b.path, undefined, { sensitivity: 'base', numeric: true }));
    return (
        <ul className="max-h-72 space-y-1 overflow-y-auto" aria-label="Destinations">
            <li>
                <Button
                    variant={current == null ? 'secondary' : 'ghost'}
                    className="w-full justify-start"
                    disabled={disabled || current == null}
                    onClick={() => onPick(null)}
                >
                    All files (top level)
                </Button>
            </li>
            {options.map((folder) => (
                <li key={folder.id}>
                    <Button
                        variant={current === folder.id ? 'secondary' : 'ghost'}
                        className="w-full justify-start truncate"
                        disabled={disabled || current === folder.id}
                        onClick={() => onPick(folder.id)}
                    >
                        <Folder aria-hidden className="mr-2 h-4 w-4 shrink-0" />
                        {folder.path}
                    </Button>
                </li>
            ))}
        </ul>
    );
}

export default FolderBrowser;
