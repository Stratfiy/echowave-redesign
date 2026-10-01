/**
 * The DPA is asked for on the first signed-in screen, not only before money.
 *
 * What matters: an account that owes an agreement sees the dialog without
 * going anywhere; one that owes nothing never does; a staffer acting as a
 * customer never does; and "Not now" really closes it.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ list: vi.fn(), accept: vi.fn() }));
const auth = vi.hoisted(() => ({ value: { user: { id: 1 }, loading: false } as unknown }));
const staff = vi.hoisted(() => ({ value: false }));

vi.mock("@/client/sdk.gen", () => ({
    listAgreementsApiV1PrivacyAgreementsGet: api.list,
    acceptAgreementApiV1PrivacyAgreementsAcceptPost: api.accept,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => auth.value }));
vi.mock("@/components/auth/ImpersonationBanner", () => ({
    isImpersonating: () => staff.value,
}));

import { AgreementsGate } from "../AgreementsGate";

const DPA = {
    key: "dpa",
    title: "Data Processing Agreement",
    version: "2026-09",
    url: "/trust#dpa",
    required: true,
};

function owes(outstanding: string[]) {
    api.list.mockResolvedValue({ data: { agreements: [DPA], outstanding } });
}

beforeEach(() => {
    vi.clearAllMocks();
    auth.value = { user: { id: 1 }, loading: false };
    staff.value = false;
});

describe("AgreementsGate", () => {
    it("asks for an outstanding DPA on its own", async () => {
        owes(["dpa"]);
        render(<AgreementsGate />);
        expect(
            await screen.findByText(/I agree to the Data Processing Agreement/),
        ).toBeTruthy();
    });

    it("stays closed when nothing is owed", async () => {
        owes([]);
        render(<AgreementsGate />);
        await waitFor(() => expect(api.list).toHaveBeenCalled());
        expect(screen.queryByText(/Before you continue/)).toBeNull();
    });

    it("never asks a staffer who is impersonating", async () => {
        staff.value = true;
        owes(["dpa"]);
        render(<AgreementsGate />);
        await new Promise((resolve) => setTimeout(resolve, 20));
        expect(api.list).not.toHaveBeenCalled();
        expect(screen.queryByText(/Before you continue/)).toBeNull();
    });

    it("does not ask before anybody is signed in", async () => {
        auth.value = { user: null, loading: false };
        owes(["dpa"]);
        render(<AgreementsGate />);
        await new Promise((resolve) => setTimeout(resolve, 20));
        expect(api.list).not.toHaveBeenCalled();
    });

    it("closes on Not now", async () => {
        owes(["dpa"]);
        render(<AgreementsGate />);
        fireEvent.click(await screen.findByRole("button", { name: "Not now" }));
        await waitFor(() =>
            expect(screen.queryByText(/Before you continue/)).toBeNull(),
        );
    });

    it("records the acceptance and closes", async () => {
        owes(["dpa"]);
        api.accept.mockResolvedValue({ data: {} });
        render(<AgreementsGate />);
        fireEvent.click(await screen.findByRole("checkbox"));
        owes([]);
        fireEvent.click(screen.getByRole("button", { name: "Agree and continue" }));
        await waitFor(() =>
            expect(api.accept).toHaveBeenCalledWith({ body: { agreement: "dpa" } }),
        );
        await waitFor(() =>
            expect(screen.queryByText(/Before you continue/)).toBeNull(),
        );
    });
});
