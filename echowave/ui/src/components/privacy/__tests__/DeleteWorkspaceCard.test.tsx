/**
 * Closing a workspace from the Privacy page.
 *
 * What matters: nobody but an owner sees it (the server says 403 and the card
 * draws nothing); the button stays disabled until the name is typed exactly;
 * a scheduled deletion shows its date and can be cancelled.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), del: vi.fn() }));

vi.mock("@/client/client.gen", () => ({
    client: { get: api.get, post: api.post, delete: api.del },
}));

import { DeleteWorkspaceCard } from "../DeleteWorkspaceCard";

const OPEN = { scheduled: null, confirmation_phrase: "Acme Dental", grace_days: 7 };
const SCHEDULED = {
    ...OPEN,
    scheduled: {
        id: 1,
        requested_at: "2026-10-01T10:00:00Z",
        deletes_at: "2026-10-08T10:00:00Z",
    },
};

beforeEach(() => vi.clearAllMocks());

describe("DeleteWorkspaceCard", () => {
    it("draws nothing for somebody who is not an owner", async () => {
        api.get.mockResolvedValue({ error: { detail: "Owner role required" } });
        const { container } = render(<DeleteWorkspaceCard />);
        await waitFor(() => expect(api.get).toHaveBeenCalled());
        expect(container.textContent).toBe("");
    });

    it("needs the workspace name typed exactly", async () => {
        api.get.mockResolvedValue({ data: OPEN });
        render(<DeleteWorkspaceCard />);
        const button = await screen.findByRole("button", { name: "Delete in 7 days" });
        expect((button as HTMLButtonElement).disabled).toBe(true);

        fireEvent.change(screen.getByLabelText(/to confirm/), {
            target: { value: "acme dental" },
        });
        expect((button as HTMLButtonElement).disabled).toBe(true);

        fireEvent.change(screen.getByLabelText(/to confirm/), {
            target: { value: "Acme Dental" },
        });
        expect((button as HTMLButtonElement).disabled).toBe(false);

        api.post.mockResolvedValue({ data: {} });
        api.get.mockResolvedValue({ data: SCHEDULED });
        fireEvent.click(button);
        await waitFor(() =>
            expect(api.post).toHaveBeenCalledWith({
                url: "/api/v1/privacy/workspace/closure",
                body: { confirm: "Acme Dental" },
            }),
        );
        expect(await screen.findByText(/will be deleted on/)).toBeTruthy();
    });

    it("shows the date and cancels a scheduled deletion", async () => {
        api.get.mockResolvedValue({ data: SCHEDULED });
        render(<DeleteWorkspaceCard />);
        expect(await screen.findByText(/8 October 2026/)).toBeTruthy();

        api.del.mockResolvedValue({ data: { cancelled: true } });
        api.get.mockResolvedValue({ data: OPEN });
        fireEvent.click(screen.getByRole("button", { name: "Cancel the deletion" }));
        await waitFor(() => expect(api.del).toHaveBeenCalled());
        expect(await screen.findByRole("button", { name: "Delete in 7 days" })).toBeTruthy();
    });
});
