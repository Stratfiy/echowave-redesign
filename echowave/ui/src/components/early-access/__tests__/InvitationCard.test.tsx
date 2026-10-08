import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InvitationCard } from "../InvitationCard";

const api = vi.hoisted(() => ({ status: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({
    inviteStatusApiV1PublicEarlyAccessInvitesCodeGet: api.status,
    joinWaitlistApiV1PublicEarlyAccessWaitlistPost: vi.fn(),
    earlyAccessLanguagesApiV1PublicEarlyAccessLanguagesGet: vi.fn().mockResolvedValue({ data: [] }),
}));

beforeEach(() => api.status.mockReset());

describe("InvitationCard", () => {
    it("shows the bound address and expiry, and accepts through sign-up", async () => {
        api.status.mockResolvedValue({
            data: { state: "valid", email_hint: "n*****@clinic.in", expires_at: "2026-10-14T10:00:00Z", code: "ABCD-2345" },
        });
        render(<InvitationCard code="abcd-2345" />);
        expect(await screen.findByText("You are invited to Decibyl")).toBeTruthy();
        expect(screen.getByText(/For n\*\*\*\*\*@clinic.in/)).toBeTruthy();
        expect(screen.getByText(/Valid until/)).toBeTruthy();
        const accept = screen.getByRole("link", { name: "Accept invitation" });
        expect(accept.getAttribute("href")).toBe("/auth/signup?invite=ABCD-2345");
        expect(api.status).toHaveBeenCalledWith({ path: { code: "abcd-2345" } });
    });

    it.each([
        ["expired", "This invitation has expired"],
        ["revoked", "This invitation was withdrawn"],
        ["used", "This invitation has been used"],
        ["invalid", "We could not find this invitation"],
    ])("never shows the way in for a %s link, and offers a new invitation", async (state, title) => {
        api.status.mockResolvedValue({ data: { state } });
        render(<InvitationCard code="X" />);
        expect(await screen.findByText(title)).toBeTruthy();
        expect(screen.queryByRole("link", { name: "Accept invitation" })).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Request a new invitation" }));
        expect(await screen.findByTestId("waitlist-form")).toBeTruthy();
    });

    it("offers a retry when the check itself fails, not a verdict", async () => {
        api.status.mockResolvedValueOnce({ error: { detail: "boom" } }).mockResolvedValue({ data: { state: "expired" } });
        render(<InvitationCard code="X" />);
        fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
        expect(await screen.findByText("This invitation has expired")).toBeTruthy();
    });
});
