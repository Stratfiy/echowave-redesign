import { afterEach, describe, expect, it, vi } from "vitest";

import { clearDraft, draftKey, readDraft, writeDraft } from "../drafts";

afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
});

describe("kept drafts", () => {
    it("are one per conversation", () => {
        expect(draftKey("assistant", null)).toBe("decibyl.chat.draft:assistant:main");
        expect(draftKey("assistant", "t-1")).not.toBe(draftKey("assistant", "t-2"));
        writeDraft(draftKey("assistant", "t-1"), "hello");
        expect(readDraft(draftKey("assistant", "t-1"))).toBe("hello");
        expect(readDraft(draftKey("assistant", "t-2"))).toBe("");
        clearDraft(draftKey("assistant", "t-1"));
        expect(localStorage.length).toBe(0);
    });

    it("never break the box when storage is unavailable", () => {
        vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
            throw new Error("blocked");
        });
        vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
            throw new Error("blocked");
        });
        expect(readDraft("k")).toBe("");
        expect(() => writeDraft("k", "x")).not.toThrow();
    });
});
