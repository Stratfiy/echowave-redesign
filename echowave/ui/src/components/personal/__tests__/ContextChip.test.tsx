/**
 * The context control above the composer: one compact line of what this
 * conversation uses ("Personal · Tamil · 2 files · Calendar"), tapping it
 * shows each source with a switch, and turning one off is sent for this
 * conversation only. Hidden while the flag is off.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getContext = vi.hoisted(() => vi.fn());
const putContext = vi.hoisted(() => vi.fn());
const flags = vi.hoisted(() => ({ on: true }));
vi.mock("@/client/sdk.gen", () => ({
    conversationContextApiV1PersonalContextGet: getContext,
    chooseContextApiV1PersonalContextPut: putContext,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({ useFeature: () => flags.on }));

import { ContextChip } from "../ContextChip";

const context = (over: Record<string, unknown> = {}) => ({
    chip: "Personal · Tamil · Acme · 2 files · Calendar",
    left_out: 0,
    space: "workspace",
    sources: [
        { id: "personal", kind: "personal", label: "Personal", detail: "2 preferences · Tamil · only you", included: true },
        { id: "workspace", kind: "workspace", label: "Acme", detail: "4 confirmed facts", included: true },
        { id: "files", kind: "files", label: "Files in this conversation", detail: "2 files", included: true },
        { id: "app:googlecalendar", kind: "app", label: "Calendar", detail: "Connected app", included: true },
    ],
    ...over,
});

beforeEach(() => {
    getContext.mockReset();
    putContext.mockReset();
    flags.on = true;
});

describe("ContextChip", () => {
    it("shows the line and opens the sources in place", async () => {
        getContext.mockResolvedValue({ data: context() });
        render(<ContextChip threadId="t1" />);
        const chip = await screen.findByRole("button", { name: /Personal · Tamil · Acme · 2 files · Calendar/ });
        expect(getContext).toHaveBeenCalledWith({ query: { thread_id: "t1" } });
        expect(screen.queryByRole("group", { name: "What this conversation uses" })).toBeNull();
        fireEvent.click(chip);
        expect(screen.getByRole("group", { name: "What this conversation uses" })).toBeTruthy();
        expect(screen.getAllByTestId("context-source")).toHaveLength(4);
        expect(screen.getByText("2 preferences · Tamil · only you")).toBeTruthy();
    });

    it("leaves a source out of this conversation and shows it", async () => {
        getContext.mockResolvedValue({ data: context() });
        putContext.mockResolvedValue({
            data: context({
                chip: "Acme · 2 files · Calendar",
                left_out: 1,
                sources: context().sources.map((s) => (s.id === "personal" ? { ...s, included: false } : s)),
            }),
        });
        render(<ContextChip threadId="t1" />);
        fireEvent.click(await screen.findByRole("button", { name: /Personal · Tamil/ }));
        fireEvent.click(screen.getByRole("switch", { name: "Use Personal" }));
        await waitFor(() =>
            expect(putContext).toHaveBeenCalledWith({
                body: { thread_id: "t1", source: "personal", included: false },
            }),
        );
        expect(await screen.findByText(/1 left out/)).toBeTruthy();
        expect((screen.getByRole("switch", { name: "Use Personal" }) as HTMLInputElement).checked).toBe(false);
    });

    it("a refused change is said, and the switch stays as it was", async () => {
        getContext.mockResolvedValue({ data: context() });
        putContext.mockResolvedValue({ error: { detail: "That is not one of this conversation's sources." } });
        render(<ContextChip threadId="t1" />);
        fireEvent.click(await screen.findByRole("button", { name: /Personal/ }));
        fireEvent.click(screen.getByRole("switch", { name: "Use Calendar" }));
        expect((await screen.findByRole("alert")).textContent).toContain("not one of");
        expect((screen.getByRole("switch", { name: "Use Calendar" }) as HTMLInputElement).checked).toBe(true);
    });

    it("draws nothing while the flag is off", () => {
        flags.on = false;
        const { container } = render(<ContextChip threadId="t1" />);
        expect(container.innerHTML).toBe("");
        expect(getContext).not.toHaveBeenCalled();
    });
});
