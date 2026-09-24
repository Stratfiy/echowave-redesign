/**
 * The list under the composer. A new chat is not in it until its first
 * line exists, so it is shown as itself while it is fresh; the original
 * conversation is in it like any other, named for what it is.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const list = vi.hoisted(() => vi.fn());
vi.mock("@/client/sdk.gen", () => ({ threadsApiV1TimelineThreadsGet: list }));

import { ago, ThreadList, titleOf } from "../ThreadList";

const THREADS = [
    { thread_id: "t-2", title: "draft a reply to the Kaggle mail", last_at: "2026-09-17T18:00:00Z", messages: 4 },
    { thread_id: null, title: "Hi", last_at: "2026-09-17T15:00:00Z", messages: 124 },
];

beforeEach(() => {
    list.mockReset();
    list.mockResolvedValue({ data: { threads: THREADS } });
});

describe("the chat list", () => {
    it("lists the chats by first line, the original among them", async () => {
        render(<ThreadList current={null} onPick={() => {}} onNew={() => {}} />);
        expect(await screen.findByRole("button", { name: "draft a reply to the Kaggle mail" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Hi" })).toBeTruthy();
    });

    it("marks the one on screen", async () => {
        render(<ThreadList current="t-2" onPick={() => {}} onNew={() => {}} />);
        const active = await screen.findByRole("button", { name: "draft a reply to the Kaggle mail" });
        expect(active.getAttribute("aria-current")).toBe("true");
        expect(screen.getByRole("button", { name: "Hi" }).getAttribute("aria-current")).toBeNull();
    });

    it("shows a brand-new chat as itself until it has a first line", async () => {
        render(<ThreadList current="fresh-1" onPick={() => {}} onNew={() => {}} />);
        await screen.findByRole("button", { name: "Hi" });
        // Two "New chat"s: the button, and the marker for where you are.
        expect(screen.getAllByText("New chat")).toHaveLength(2);
    });

    it("New and pick hand back what was chosen", async () => {
        const onNew = vi.fn();
        const onPick = vi.fn();
        render(<ThreadList current={null} onPick={onPick} onNew={onNew} />);
        fireEvent.click(await screen.findByRole("button", { name: "New chat" }));
        expect(onNew).toHaveBeenCalled();
        fireEvent.click(screen.getByRole("button", { name: "draft a reply to the Kaggle mail" }));
        expect(onPick).toHaveBeenCalledWith("t-2");
        fireEvent.click(screen.getByRole("button", { name: "Hi" }));
        expect(onPick).toHaveBeenCalledWith(null);
    });

    it("a list that could not be read is empty, not an error over the chat", async () => {
        list.mockResolvedValue({ error: { detail: "nope" } });
        render(<ThreadList current={null} onPick={() => {}} onNew={() => {}} />);
        expect(await screen.findByRole("button", { name: "New chat" })).toBeTruthy();
        expect(screen.queryByText("nope")).toBeNull();
    });
});

describe("words for a chat", () => {
    it("names the original for what it is when it has no first line", () => {
        expect(titleOf({ thread_id: null, title: "", last_at: "", messages: 0 })).toBe("The first chat");
        expect(titleOf({ thread_id: "x", title: "", last_at: "", messages: 0 })).toBe("Untitled chat");
    });

    it("trims a long first line", () => {
        const title = "a".repeat(80);
        expect(titleOf({ thread_id: "x", title, last_at: "", messages: 1 })).toHaveLength(58);
    });

    it("says roughly when", () => {
        const now = new Date("2026-09-17T18:00:00Z");
        expect(ago("2026-09-17T17:59:40Z", now)).toBe("just now");
        expect(ago("2026-09-17T17:30:00Z", now)).toBe("30m ago");
        expect(ago("2026-09-17T13:00:00Z", now)).toBe("5h ago");
        expect(ago("2026-09-14T18:00:00Z", now)).toBe("3d ago");
    });
});

describe("the chat list's shape", () => {
    it("is one row that scrolls sideways, so it cannot push the composer up the screen", async () => {
        render(<ThreadList current={null} onPick={() => {}} onNew={() => {}} />);
        await screen.findByRole("button", { name: "Hi" });
        const row = screen.getByTestId("thread-list");
        expect(row.className).toContain("flex-nowrap");
        expect(row.className).toContain("overflow-x-auto");
        expect(row.className.split(" ")).not.toContain("flex-wrap");
        for (const button of row.querySelectorAll("button")) expect(button.className).toContain("shrink-0");
    });
});
