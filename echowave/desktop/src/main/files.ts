/**
 * Files from this computer into a Decibyl chat.
 *
 * - **Attach**: the person picks files or a folder in the system dialog (the
 *   dialog is main's, never a path a page supplied); each file is read here
 *   and handed to the web app, which uploads it the way it uploads any
 *   attachment. A folder is read to a fixed depth and count, and what was
 *   left out is said, not dropped silently.
 * - **Watched folder**: off until the person picks one. A new file that
 *   lands there (an invoice saved from mail, a scan) raises a notification;
 *   nothing is uploaded until the person clicks it. Only files that appear
 *   after watching started count, partial downloads are ignored, and the
 *   folder is never read recursively.
 */

import fs from 'node:fs';
import path from 'node:path';

export const MAX_FILE_BYTES = 25 * 1024 * 1024;
export const MAX_FILES = 30;
export const MAX_DEPTH = 3;

export interface PickedFile {
    name: string;
    /** Path relative to the folder picked, for files from a folder. */
    relativePath: string;
    size: number;
    type: string;
    /** base64 */
    data: string;
}

export interface PickResult {
    files: PickedFile[];
    /** What was not read and why, one line each. */
    skipped: string[];
}

const TYPES: Record<string, string> = {
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.txt': 'text/plain',
    '.md': 'text/markdown',
    '.csv': 'text/csv',
    '.json': 'application/json',
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
};

export function typeOf(name: string): string {
    return TYPES[path.extname(name).toLowerCase()] ?? 'application/octet-stream';
}

/** Dotfiles, OS litter and downloads still in progress. */
export function isIgnorable(name: string): boolean {
    return (
        name.startsWith('.') ||
        name.startsWith('~$') ||
        /\.(crdownload|part|partial|download|tmp|lock)$/i.test(name) ||
        name === 'Thumbs.db' ||
        name === 'desktop.ini'
    );
}

function readOne(full: string, relativePath: string, out: PickResult): void {
    if (out.files.length >= MAX_FILES) {
        out.skipped.push(`${relativePath}: more than ${MAX_FILES} files`);
        return;
    }
    const stat = fs.statSync(full);
    if (stat.size > MAX_FILE_BYTES) {
        out.skipped.push(`${relativePath}: larger than ${MAX_FILE_BYTES / 1024 / 1024} MB`);
        return;
    }
    out.files.push({
        name: path.basename(full),
        relativePath,
        size: stat.size,
        type: typeOf(full),
        data: fs.readFileSync(full).toString('base64'),
    });
}

function walk(dir: string, base: string, depth: number, out: PickResult): void {
    for (const entry of fs
        .readdirSync(dir, { withFileTypes: true })
        .sort((a, b) => a.name.localeCompare(b.name))) {
        const full = path.join(dir, entry.name);
        const rel = path.relative(base, full).split(path.sep).join('/');
        if (isIgnorable(entry.name) || entry.isSymbolicLink()) continue;
        if (entry.isDirectory()) {
            if (depth >= MAX_DEPTH) out.skipped.push(`${rel}/: deeper than ${MAX_DEPTH} folders`);
            else walk(full, base, depth + 1, out);
        } else if (entry.isFile()) {
            readOne(full, rel, out);
        }
    }
}

/** Read what the dialog returned. Paths come from the OS dialog only. */
export function readPicked(paths: string[]): PickResult {
    const out: PickResult = { files: [], skipped: [] };
    for (const p of paths) {
        const stat = fs.statSync(p);
        if (stat.isDirectory()) walk(p, path.dirname(p), 1, out);
        else if (stat.isFile()) readOne(p, path.basename(p), out);
    }
    return out;
}

export interface WatchedFile {
    name: string;
    fullPath: string;
    size: number;
}

/**
 * Watch one folder for new files. `onNew` is called once per file, after its
 * size has stopped changing (a file still being written is not "new" yet).
 */
export function watchFolder(
    folder: string,
    onNew: (file: WatchedFile) => void,
    opts: { settleMs?: number } = {},
): () => void {
    const settleMs = opts.settleMs ?? 1500;
    const known = new Set(fs.readdirSync(folder));
    const pending = new Map<string, NodeJS.Timeout>();

    const check = (name: string, lastSize = -1) => {
        const full = path.join(folder, name);
        let stat: fs.Stats;
        try {
            stat = fs.statSync(full);
        } catch {
            return; // gone again (renamed, deleted)
        }
        if (!stat.isFile()) return;
        if (stat.size !== lastSize) {
            pending.set(
                name,
                setTimeout(() => check(name, stat.size), settleMs),
            );
            return;
        }
        pending.delete(name);
        if (known.has(name)) return;
        known.add(name);
        if (stat.size <= MAX_FILE_BYTES) onNew({ name, fullPath: full, size: stat.size });
    };

    const watcher = fs.watch(folder, { persistent: false }, (_event, filename) => {
        const name = filename ? String(filename) : '';
        if (!name || isIgnorable(name) || name.includes('/') || name.includes('\\')) return;
        if (known.has(name) || pending.has(name)) return;
        check(name);
    });
    return () => {
        watcher.close();
        for (const t of pending.values()) clearTimeout(t);
        pending.clear();
    };
}

/** Read one watched file, only if it is still directly inside the folder. */
export function readWatched(folder: string, name: string): PickedFile {
    const full = path.resolve(folder, name);
    if (path.dirname(full) !== path.resolve(folder) || isIgnorable(name))
        throw new Error('Not a file in the watched folder.');
    const out: PickResult = { files: [], skipped: [] };
    readOne(full, name, out);
    if (!out.files[0]) throw new Error(out.skipped[0] ?? 'Could not read it.');
    return out.files[0];
}
