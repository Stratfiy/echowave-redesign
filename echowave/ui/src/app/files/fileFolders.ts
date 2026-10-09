/**
 * Folders on the Files page, and reading what was dropped on it.
 *
 * "File folder" throughout: in this codebase a bare "folder" is a channel
 * (`folder_id`, listFoldersApiV1FolderGet), and these never are. A file
 * folder only organises; it does not change who reads a file.
 */

import type { FileFolderSchema } from '@/client/types.gen';

export type FileFolder = FileFolderSchema;

/** The drag payload for a file being moved between folders on this page. */
export const FILE_DRAG_TYPE = 'application/x-decibyl-file';

/** The folders directly inside `parentId` (null: the top level), by name. */
export function childrenOf(folders: readonly FileFolder[], parentId: number | null): FileFolder[] {
    return folders
        .filter((f) => (f.parent_id ?? null) === parentId)
        .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base', numeric: true }));
}

/** From the top level down to `folderId`. A folder whose parent is missing
 *  (deleted elsewhere) ends the chain rather than looping. */
export function breadcrumbs(folders: readonly FileFolder[], folderId: number | null): FileFolder[] {
    const byId = new Map(folders.map((f) => [f.id, f]));
    const chain: FileFolder[] = [];
    let current = folderId == null ? undefined : byId.get(folderId);
    while (current && chain.length < 64) {
        chain.unshift(current);
        current = current.parent_id == null ? undefined : byId.get(current.parent_id);
    }
    return chain;
}

/** `folderId` and every folder below it: where a folder cannot be moved to. */
export function subtree(folders: readonly FileFolder[], folderId: number): Set<number> {
    const out = new Set<number>([folderId]);
    let grew = true;
    while (grew) {
        grew = false;
        for (const f of folders) {
            if (f.parent_id != null && out.has(f.parent_id) && !out.has(f.id)) {
                out.add(f.id);
                grew = true;
            }
        }
    }
    return out;
}

/** One file from a drop, with the folders it sat in relative to the drop. */
export type DroppedFile = { file: File; path: string[] };

type Entry = {
    isFile: boolean;
    isDirectory: boolean;
    name: string;
    file?: (ok: (file: File) => void, fail: (error: unknown) => void) => void;
    createReader?: () => { readEntries: (ok: (entries: Entry[]) => void, fail: (error: unknown) => void) => void };
};

async function walk(entry: Entry, path: string[], out: DroppedFile[]): Promise<void> {
    if (entry.isFile && entry.file) {
        const file = await new Promise<File>((ok, fail) => entry.file!(ok, fail));
        out.push({ file, path });
        return;
    }
    if (entry.isDirectory && entry.createReader) {
        const reader = entry.createReader();
        const here = [...path, entry.name];
        // readEntries hands a directory over in batches, and an empty batch
        // is the only signal that there are no more. Reading once loses
        // everything past the first hundred files of a large folder.
        for (;;) {
            const batch = await new Promise<Entry[]>((ok, fail) => reader.readEntries(ok, fail));
            if (batch.length === 0) break;
            for (const child of batch) await walk(child, here, out);
        }
    }
}

/**
 * Every file in a drop, keeping the structure of any folder dropped from the
 * desktop: dropping `Contracts/` that holds `2026/acme.pdf` gives
 * `{ file: acme.pdf, path: ['Contracts', '2026'] }`.
 *
 * The entries are taken synchronously, before the first await: the browser
 * empties the DataTransfer once the drop event returns. Without
 * webkitGetAsEntry (an old browser, a test) the plain file list is used and
 * folders arrive flattened, which loses structure but never a file.
 */
export function readDrop(data: DataTransfer | null | undefined): Promise<DroppedFile[]> {
    if (!data) return Promise.resolve([]);
    const items = Array.from(data.items ?? []);
    const entries = items
        .filter((item) => item.kind === 'file')
        .map((item) => (typeof item.webkitGetAsEntry === 'function' ? (item.webkitGetAsEntry() as unknown as Entry | null) : null));
    if (entries.length === 0 || entries.some((entry) => entry == null)) {
        return Promise.resolve(Array.from(data.files ?? []).map((file) => ({ file, path: [] })));
    }
    return (async () => {
        const out: DroppedFile[] = [];
        for (const entry of entries as Entry[]) await walk(entry, [], out);
        return out;
    })();
}

/** Whether a drag carries files from the desktop (not a row moved on the page). */
export function carriesDesktopFiles(data: DataTransfer | null | undefined): boolean {
    const types = Array.from(data?.types ?? []);
    return types.includes('Files') && !types.includes(FILE_DRAG_TYPE);
}

/** Whether a drag is a file row being moved on this page. */
export function carriesFileRow(data: DataTransfer | null | undefined): boolean {
    return Array.from(data?.types ?? []).includes(FILE_DRAG_TYPE);
}
