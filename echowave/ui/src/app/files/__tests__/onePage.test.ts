/**
 * Files is one page, reached from the main sidebar only. Settings does not
 * list it, nothing links to its old address, and an old link or bookmark
 * still lands on it.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { HOMES } from "@/components/layout/v2/homes";
import { searchSettings, SETTINGS_SEARCH, SETTINGS_SECTIONS, SHELL_SECTIONS } from "@/components/settings/sections";
import { REDESIGN_REDIRECTS } from "@/lib/redesignRedirects";

const SRC = resolve(process.cwd(), "src");

function sourceFiles(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) {
            return name === "__tests__" || name === "client" ? [] : sourceFiles(path);
        }
        return /\.(ts|tsx)$/.test(name) ? [path] : [];
    });
}

describe("Files is one page", () => {
    it("is a door on the rail, at /files", () => {
        const files = HOMES.find((home) => home.id === "files");
        expect(files?.url).toBe("/files");
        expect(HOMES.filter((home) => !home.flag).map((home) => home.title)).toEqual(["Chat", "Today", "Files"]);
    });

    it("is listed nowhere in Settings", () => {
        const all = [
            ...SETTINGS_SECTIONS.map((s) => [s.title, s.href]),
            ...SHELL_SECTIONS.map((s) => [s.title, s.href]),
            ...SETTINGS_SEARCH.map((s) => [s.label, s.href]),
        ];
        for (const [title, href] of all) {
            expect(title).not.toBe("Files");
            expect(href).not.toMatch(/^\/(files|settings\/knowledge)\b/);
        }
        // Searching Settings for the words people use for it finds no stale
        // door into a page Settings no longer holds.
        for (const word of ["files", "documents", "knowledge"]) {
            const hits = searchSettings(word, SHELL_SECTIONS);
            expect(hits.map((h) => h.href).filter((href) => /knowledge|files/.test(href))).toEqual([]);
        }
    });

    it("sends the old address to /files, in one hop", () => {
        const old = REDESIGN_REDIRECTS.find((r) => r.source === "/settings/knowledge");
        expect(old?.destination).toBe("/files");
        expect(REDESIGN_REDIRECTS.some((r) => r.source === "/files")).toBe(false);
    });

    it("is linked to by its own address everywhere in the app", () => {
        const offenders = sourceFiles(SRC)
            .filter((path) => !path.endsWith("redesignRedirects.ts"))
            .filter((path) => readFileSync(path, "utf8").includes("/settings/knowledge"));
        expect(offenders).toEqual([]);
    });
});
