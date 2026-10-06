import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { REDESIGN_REDIRECTS } from "../redesignRedirects";

const APP = resolve(process.cwd(), "src/app");

/** The page file a path would render, with :param(...) segments as [param]. */
function pageFor(path: string): string {
    const bare = path.split("?")[0];
    const segments = bare
        .split("/")
        .filter(Boolean)
        .map((segment) => segment.replace(/^:(\w+)(\(.*\))?$/, "[$1]"));
    return resolve(APP, ...segments, "page.tsx");
}

describe("the redesign's redirects", () => {
    it.each(REDESIGN_REDIRECTS.map((r) => [r.source, r.destination]))("%s lands on a page that exists (%s)", (_, destination) => {
        expect(existsSync(pageFor(destination)), destination).toBe(true);
    });

    it.each(REDESIGN_REDIRECTS.map((r) => [r.source]))("%s is no longer a page of its own", (source) => {
        // A redirect in next.config runs before the page, so a page left
        // behind would be unreachable code.
        expect(existsSync(pageFor(source)), source).toBe(false);
    });

    it("lists each old address once", () => {
        const sources = REDESIGN_REDIRECTS.map((r) => r.source);
        expect(new Set(sources).size).toBe(sources.length);
    });
});
