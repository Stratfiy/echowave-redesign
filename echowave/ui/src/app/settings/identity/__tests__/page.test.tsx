/**
 * Settings -> Decibyl identity (screens 23 and 24). Copy appears only for
 * an active address; a taken name says so without naming anyone; the
 * virtual card collects interest only; the number payment flow is
 * explained with the amount as a marked placeholder; a failed source is
 * named, and the request waits on the payment decision.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EmailIdentityView, PhoneIdentityView } from "@/client/types.gen";

const api = vi.hoisted(() => ({
    email: vi.fn(),
    phone: vi.fn(),
    check: vi.fn(),
    reserve: vi.fn(),
    interest: vi.fn(),
    cards: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({ useFeature: () => true }));
vi.mock("@/client/sdk.gen", () => ({
    myEmailIdentityApiV1MeEmailIdentityGet: api.email,
    myPhoneIdentityApiV1MePhoneIdentityGet: api.phone,
    checkAliasApiV1MeEmailIdentityCheckPost: api.check,
    reserveAliasApiV1MeEmailIdentityReservePost: api.reserve,
    cardInterestApiV1MeCardInterestPut: api.interest,
    myIdentityCardsApiV1MeIdentityCardsGet: api.cards,
    provisionAliasApiV1MeEmailIdentityProvisionPost: vi.fn(),
    releaseAliasApiV1MeEmailIdentityReleasePost: vi.fn(),
    proposeSendApiV1MeEmailIdentitySendPost: vi.fn(),
    proposeNumberApiV1MePhoneIdentityRequestPost: vi.fn(),
    settleActionApiV1TimelineActionsSettlePost: vi.fn(),
    sayWhetherItArrivedApiV1MeOutcomesEventIdPost: vi.fn(),
}));

import IdentityPage from "../page";

const EMAIL: EmailIdentityView = {
    state: "unallocated",
    domain: "decibyl.ai",
    next_step: "Choose an address.",
    revision: 0,
    inbound: { ready: true },
    outbound: { ready: false, reason: "Sending from Decibyl addresses is not set up on this deployment yet." },
    card_interest: false,
    threads: [],
};

const PHONE: PhoneIdentityView = {
    state: "eligible",
    next_step: "Request a number for one of your helpers.",
    verification_status: "carrier_approved",
    sources: { verification: "ok", numbers: "ok", autopay: "ok", helpers: "failed" },
    numbers: [],
    helpers: null,
    payment: {
        decided: false,
        who_pays: "Who pays for numbers in the beta is not decided yet.",
        amount: "[PLACEHOLDER: monthly amount, founder decision pending]",
        steps: ["A number is rented from a telephone carrier, month by month."],
    },
    request: { available: false, reason: "Requesting a number opens once who pays for numbers in the beta is decided." },
    chat_needs_number: false,
    is_admin: true,
};

beforeEach(() => {
    api.email.mockResolvedValue({ data: EMAIL });
    api.phone.mockResolvedValue({ data: PHONE });
    api.cards.mockResolvedValue({ data: { cards: [] } });
});
afterEach(() => {
    cleanup();
    Object.values(api).forEach((fn) => fn.mockReset());
});

describe("Email", () => {
    it("has no Copy until the address is active", async () => {
        render(<IdentityPage />);
        expect(await screen.findByTestId("email-state")).toBeTruthy();
        expect(screen.queryByRole("button", { name: /Copy/ })).toBeNull();
    });

    it("an active address can be copied and says sending needs setup", async () => {
        api.email.mockResolvedValue({ data: { ...EMAIL, state: "active", alias: "meera", address: "meera@decibyl.ai", next_step: null } });
        render(<IdentityPage />);
        expect((await screen.findByTestId("email-address")).textContent).toBe("meera@decibyl.ai");
        expect(screen.getByRole("button", { name: /Copy/ })).toBeTruthy();
        expect(screen.getByText(/Needs setup: Sending from Decibyl addresses/)).toBeTruthy();
    });

    it("a taken name says so and offers no reserve", async () => {
        api.check.mockResolvedValue({ data: { alias: "meera", available: false, reason: "taken", message: "That address is taken. Try another." } });
        render(<IdentityPage />);
        fireEvent.change(await screen.findByLabelText("Choose a name"), { target: { value: "meera" } });
        fireEvent.click(screen.getByRole("button", { name: "Check" }));
        expect(await screen.findByText("That address is taken. Try another.")).toBeTruthy();
        expect(screen.queryByRole("button", { name: /Reserve/ })).toBeNull();
    });

    it("the virtual card collects interest only", async () => {
        api.interest.mockResolvedValue({ data: { interested: true } });
        render(<IdentityPage />);
        fireEvent.click(await screen.findByRole("button", { name: "I'm interested" }));
        await waitFor(() => expect(api.interest).toHaveBeenCalledWith({ body: { interested: true } }));
        expect(screen.getByText(/no card details are asked for/)).toBeTruthy();
        expect(screen.queryByLabelText(/card number/i)).toBeNull();
    });
});

describe("Phone", () => {
    it("explains paying for a number with the amount as a marked placeholder", async () => {
        render(<IdentityPage />);
        const box = await screen.findByTestId("number-payment");
        expect(box.textContent).toContain("rented from a telephone carrier");
        expect(box.querySelector('[data-placeholder="number-amount"]')?.textContent).toContain("PLACEHOLDER");
    });

    it("names a failed source and waits on the payment decision", async () => {
        render(<IdentityPage />);
        expect(await screen.findByText(/Could not check: helpers/)).toBeTruthy();
        expect(screen.getByText(/who pays for numbers in the beta is decided/)).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Review the request" })).toBeNull();
        expect(screen.getByText("Chat and everything else work without a number.")).toBeTruthy();
    });
});
