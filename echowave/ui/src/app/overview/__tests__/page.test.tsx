/**
 * Decibyl is a thread, not a screen with tabs.
 *
 * Five tabs sat above it -- Messages, Tasks, Requests, Memory, About -- and
 * two of them were doors the sidebar already holds. What the assistant is
 * belongs beside the conversation, the way a bot's About does, not a tab away.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
    usePathname: () => "/overview",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/auth", () => ({
    useAuth: () => ({ user: { displayName: "Asha Rao" }, loading: false }),
}));

vi.mock("@/components/home/HomeAboveTheFold", () => ({
    HomeAboveTheFold: () => <div data-testid="thread" />,
}));

vi.mock("@/components/home/DecibylAbout", () => ({
    DecibylAbout: () => <div data-testid="about-body" />,
}));

import OverviewPage from "../page";

afterEach(cleanup);

describe("Decibyl's own screen", () => {
    it("carries no tab strip", () => {
        render(<OverviewPage />);
        expect(screen.queryByRole("navigation", { name: "Section" })).toBeNull();
        // The doors that used to be tabs are the sidebar's, not this screen's.
        expect(screen.queryByRole("link", { name: "Tasks" })).toBeNull();
        expect(screen.queryByRole("link", { name: "Requests" })).toBeNull();
    });

    it("opens what the assistant is beside the thread, not a tab away", () => {
        render(<OverviewPage />);
        expect(screen.queryByTestId("auxiliary-panel")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "About" }));
        expect(screen.getByTestId("auxiliary-panel")).toBeTruthy();
        expect(screen.getByTestId("about-body")).toBeTruthy();
        // And the thread is still there beside it.
        expect(screen.getByTestId("thread")).toBeTruthy();
    });
});
