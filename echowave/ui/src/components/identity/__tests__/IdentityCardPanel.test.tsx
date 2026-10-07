/**
 * A person's own identity card, approved where it was asked for (launch
 * stream identity). Approve sends the version on screen; an unknown outcome
 * says so in the shared words and never offers a resend; where nobody can
 * ask the provider, the person says whether it arrived.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { IdentityCard } from "@/client/types.gen";

const settle = vi.hoisted(() => vi.fn());
const arrived = vi.hoisted(() => vi.fn());
const flags = vi.hoisted(() => ({ identity_reconciliation: false }));
vi.mock("@/client/sdk.gen", () => ({
    settleActionApiV1TimelineActionsSettlePost: settle,
    sayWhetherItArrivedApiV1MeOutcomesEventIdPost: arrived,
}));
vi.mock("@/lib/features", () => ({
    useFeature: (name: string) => Boolean((flags as Record<string, boolean>)[name]),
}));

import { IdentityCardPanel, UNKNOWN_COPY } from "../IdentityCardPanel";

const CARD: IdentityCard = {
    event_id: 41,
    action: "send_identity_email",
    label: "Send an email from meera@decibyl.ai to ravi@example.com",
    effect: "Sends from meera@decibyl.ai, your Decibyl address, not from a connected mailbox. It cannot be unsent.",
    state: "proposed",
    version: "ab12cd34ef56ab78",
    revisions: 1,
    args: { from_address: "meera@decibyl.ai", to: "ravi@example.com", subject: "Invoice", body: "Attached." },
    affected: [],
    needs_person: false,
};

afterEach(() => {
    cleanup();
    settle.mockReset();
    arrived.mockReset();
    flags.identity_reconciliation = false;
});

describe("IdentityCardPanel", () => {
    it("shows the exact account, recipient and words, and approves the version on screen", async () => {
        settle.mockResolvedValue({ data: {} });
        const onChanged = vi.fn();
        render(<IdentityCardPanel card={CARD} onChanged={onChanged} />);
        expect(screen.getByText("meera@decibyl.ai (your Decibyl address)")).toBeTruthy();
        expect(screen.getByText("ravi@example.com")).toBeTruthy();
        expect(screen.getByText(/Subject: Invoice/)).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Approve" }));
        await waitFor(() => expect(onChanged).toHaveBeenCalled());
        expect(settle.mock.calls[0][0].body).toEqual({ event_id: 41, verb: "confirm", version: "ab12cd34ef56ab78" });
    });

    it("says why when the approval is refused", async () => {
        settle.mockResolvedValue({ error: { detail: "This changed since you looked at it." } });
        render(<IdentityCardPanel card={CARD} onChanged={vi.fn()} />);
        fireEvent.click(screen.getByRole("button", { name: "Approve" }));
        await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("This changed since you looked at it."));
    });

    it("offers Undo while armed", async () => {
        settle.mockResolvedValue({ data: {} });
        render(<IdentityCardPanel card={{ ...CARD, state: "armed" }} onChanged={vi.fn()} />);
        fireEvent.click(screen.getByRole("button", { name: "Undo" }));
        await waitFor(() => expect(settle).toHaveBeenCalled());
        expect(settle.mock.calls[0][0].body.verb).toBe("undo");
    });

    it("reads an unknown outcome in the shared words, with no way to send again", () => {
        render(<IdentityCardPanel card={{ ...CARD, state: "outcome_unknown" }} onChanged={vi.fn()} />);
        expect(screen.getByRole("status").textContent).toContain(UNKNOWN_COPY);
        expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
        expect(screen.queryByRole("button", { name: "It arrived" })).toBeNull();
    });

    it("asks whether it arrived only when reconciliation is on and a person is needed", async () => {
        flags.identity_reconciliation = true;
        arrived.mockResolvedValue({ data: { state: "done" } });
        const onChanged = vi.fn();
        render(<IdentityCardPanel card={{ ...CARD, state: "outcome_unknown", needs_person: true }} onChanged={onChanged} />);
        fireEvent.click(screen.getByRole("button", { name: "It did not arrive" }));
        await waitFor(() => expect(onChanged).toHaveBeenCalled());
        expect(arrived.mock.calls[0][0]).toEqual({ path: { event_id: 41 }, body: { arrived: false } });
    });

    it("shows what happened once it ran", () => {
        render(<IdentityCardPanel card={{ ...CARD, state: "done", done_note: "Sent from meera@decibyl.ai to ravi@example.com." }} onChanged={vi.fn()} />);
        expect(screen.getByText("Sent from meera@decibyl.ai to ravi@example.com.")).toBeTruthy();
    });
});
