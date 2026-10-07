/** The web app's half of the desktop bridge (echowave/desktop). */

import { afterEach, describe, expect, it } from "vitest";

import { desktopBridge, noticePath, noticesToForward, toFiles } from "../desktop";

const notice = (id: number, read = false) => ({
    id,
    title: `Notice ${id}`,
    body: null,
    link: null,
    read_at: read ? "2026-10-07T00:00:00Z" : null,
});

afterEach(() => {
    delete (window as unknown as { decibylDesktop?: unknown }).decibylDesktop;
});

describe("the bridge", () => {
    it("is absent in a browser", () => {
        expect(desktopBridge()).toBeNull();
    });

    it("is present inside the desktop app", () => {
        (window as unknown as { decibylDesktop: unknown }).decibylDesktop = { isDesktop: true, notify: () => undefined };
        expect(desktopBridge()).not.toBeNull();
    });

    it("ignores something else that happens to have the name", () => {
        (window as unknown as { decibylDesktop: unknown }).decibylDesktop = { isDesktop: "yes" };
        expect(desktopBridge()).toBeNull();
    });
});

describe("native notifications", () => {
    it("does not replay old notices when the app opens", () => {
        const first = noticesToForward([notice(3), notice(2)], null);
        expect(first.forward).toEqual([]);
        expect(first.lastSeen).toBe(3);
    });

    it("raises new unread notices once, oldest first, at most three", () => {
        const items = [notice(9), notice(8), notice(7, true), notice(6), notice(5), notice(4), notice(3)];
        const { forward, lastSeen } = noticesToForward(items, 3);
        expect(forward.map((n) => n.id)).toEqual([6, 8, 9]);
        expect(lastSeen).toBe(9);
        expect(noticesToForward(items, 9).forward).toEqual([]);
    });

    it("only ever links inside the app", () => {
        expect(noticePath("/billing")).toBe("/billing");
        expect(noticePath("https://evil.example")).toBe("/");
        expect(noticePath("//evil.example")).toBe("/");
        expect(noticePath(null)).toBe("/");
    });
});

describe("files from the computer", () => {
    it("become browser Files the composer can attach", async () => {
        const [file] = toFiles({
            files: [{ name: "inv.pdf", relativePath: "Invoices/inv.pdf", size: 3, type: "application/pdf", data: btoa("pdf") }],
            skipped: [],
        });
        expect(file.name).toBe("inv.pdf");
        expect(file.type).toBe("application/pdf");
        expect(await file.text()).toBe("pdf");
    });
});
