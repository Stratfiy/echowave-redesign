/**
 * The pure half of folders on the Files page: where you are, what is here,
 * and what a drop from the desktop holds -- including a whole folder, whose
 * structure is kept.
 */
import { describe, expect, it } from "vitest";

import { breadcrumbs, carriesDesktopFiles, carriesFileRow, childrenOf, FILE_DRAG_TYPE, type FileFolder, readDrop, subtree } from "../fileFolders";

const folder = (id: number, name: string, parent_id: number | null, path: string): FileFolder => ({
    id,
    folder_uuid: `f${id}`,
    name,
    parent_id,
    path,
    file_count: 0,
    folder_count: 0,
});

const FOLDERS = [
    folder(1, "Pricing", null, "Pricing"),
    folder(2, "2026", 1, "Pricing/2026"),
    folder(3, "archive", 1, "Pricing/archive"),
    folder(4, "HR", null, "HR"),
    folder(5, "Q1", 2, "Pricing/2026/Q1"),
];

describe("where you are", () => {
    it("lists a folder's own folders by name", () => {
        expect(childrenOf(FOLDERS, null).map((f) => f.name)).toEqual(["HR", "Pricing"]);
        expect(childrenOf(FOLDERS, 1).map((f) => f.name)).toEqual(["2026", "archive"]);
    });

    it("walks from the top down to the open folder", () => {
        expect(breadcrumbs(FOLDERS, 5).map((f) => f.name)).toEqual(["Pricing", "2026", "Q1"]);
        expect(breadcrumbs(FOLDERS, null)).toEqual([]);
    });

    it("knows what a folder cannot be moved into: itself and what is below it", () => {
        expect([...subtree(FOLDERS, 1)].sort()).toEqual([1, 2, 3, 5]);
        expect([...subtree(FOLDERS, 4)]).toEqual([4]);
    });
});

/** A fake FileSystemEntry tree, handing directories over in batches the way
 *  browsers do (and ending with an empty batch). */
function fileEntry(name: string) {
    return { isFile: true, isDirectory: false, name, file: (ok: (f: File) => void) => ok(new File(["x"], name)) };
}
function dirEntry(name: string, children: unknown[]) {
    return {
        isFile: false,
        isDirectory: true,
        name,
        createReader: () => {
            const batches = [children.slice(0, 1), children.slice(1), []];
            return { readEntries: (ok: (e: unknown[]) => void) => ok(batches.shift() ?? []) };
        },
    };
}
function transfer(entries: unknown[], files: File[] = []) {
    return {
        types: ["Files"],
        files,
        items: entries.map((entry) => ({ kind: "file", webkitGetAsEntry: () => entry })),
    } as unknown as DataTransfer;
}

describe("reading a drop", () => {
    it("keeps a dropped folder's structure, every batch of it", async () => {
        const dropped = await readDrop(
            transfer([
                dirEntry("Contracts", [fileEntry("acme.pdf"), dirEntry("2026", [fileEntry("beta.docx"), fileEntry("gamma.txt")])]),
                fileEntry("loose.csv"),
            ]),
        );
        expect(dropped.map((d) => [...d.path, d.file.name].join("/"))).toEqual([
            "Contracts/acme.pdf",
            "Contracts/2026/beta.docx",
            "Contracts/2026/gamma.txt",
            "loose.csv",
        ]);
    });

    it("falls back to the plain file list where entries are not offered", async () => {
        const a = new File(["a"], "a.pdf");
        const dropped = await readDrop({ types: ["Files"], files: [a] } as unknown as DataTransfer);
        expect(dropped).toEqual([{ file: a, path: [] }]);
    });

    it("tells a row being moved apart from files from the desktop", () => {
        const desktop = { types: ["Files"] } as unknown as DataTransfer;
        const row = { types: [FILE_DRAG_TYPE, "text/plain"] } as unknown as DataTransfer;
        expect(carriesDesktopFiles(desktop)).toBe(true);
        expect(carriesFileRow(desktop)).toBe(false);
        expect(carriesDesktopFiles(row)).toBe(false);
        expect(carriesFileRow(row)).toBe(true);
    });
});
