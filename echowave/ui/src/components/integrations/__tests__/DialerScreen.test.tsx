/**
 * Connecting a dialer (CR-2): nothing is sent until the admin has read what
 * happens to the calls and said yes, the form asks only what that dialer
 * needs, a connection that has stopped importing says so, and the whole
 * screen stands down while the import is switched off.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
    list: vi.fn(),
    calls: vi.fn(),
    connect: vi.fn(),
    disconnect: vi.fn(),
    confirm: vi.fn(),
}));

vi.mock("@/client/sdk.gen", () => ({
    listDialerConnectionsApiV1DialerConnectionsGet: api.list,
    listImportedCallsApiV1DialerConnectionsCallsGet: api.calls,
    connectDialerApiV1DialerConnectionsPost: api.connect,
    disconnectDialerApiV1DialerConnectionsConnectionIdDelete: api.disconnect,
}));
vi.mock("@/components/ConfirmDialog", () => ({
    useConfirm: () => ({ confirm: api.confirm, dialog: null }),
}));

import { DIALER_CONSENT_CHECKBOX, DIALER_CONSENT_LINES } from "../dialerConsent";
import { DialerScreen } from "../DialerScreen";

const ok = (data: unknown) => ({ data, error: undefined, response: { status: 200 } });

beforeEach(() => {
    vi.clearAllMocks();
    api.list.mockResolvedValue(ok({ connections: [] }));
    api.calls.mockResolvedValue(ok({ calls: [] }));
    api.connect.mockResolvedValue(ok({ connection: {}, unverified: null }));
    api.disconnect.mockResolvedValue(ok({ removed: true }));
    api.confirm.mockResolvedValue(true);
});

describe("connecting a dialer", () => {
    it("shows what happens to the calls, and sends nothing until it is accepted", async () => {
        render(<DialerScreen />);
        await screen.findByText("Connect your dialer");
        for (const line of DIALER_CONSENT_LINES) expect(screen.getByText(line)).toBeTruthy();

        fireEvent.click(screen.getByRole("radio", { name: "Tata Smartflo" }));
        fireEvent.change(screen.getByLabelText("API token"), { target: { value: "tok-1234" } });
        const button = screen.getByRole("button", { name: "Connect Tata Smartflo" });
        expect((button as HTMLButtonElement).disabled).toBe(true);

        fireEvent.click(screen.getByLabelText(DIALER_CONSENT_CHECKBOX));
        expect((button as HTMLButtonElement).disabled).toBe(false);
        fireEvent.click(button);

        await waitFor(() => expect(api.connect).toHaveBeenCalled());
        expect(api.connect.mock.calls[0][0].body).toEqual({
            vendor: "smartflo",
            credentials: { api_token: "tok-1234" },
            label: null,
            consent_accepted: true,
        });
    });

    it("asks Exotel for its four fields, the subdomain optional", async () => {
        render(<DialerScreen />);
        await screen.findByText("Connect your dialer");
        for (const label of ["API key", "API token", "Account SID"]) {
            expect(screen.getByLabelText(label)).toBeTruthy();
        }
        expect(screen.getByLabelText("API subdomain (optional)")).toBeTruthy();
        fireEvent.click(screen.getByLabelText(DIALER_CONSENT_CHECKBOX));
        fireEvent.change(screen.getByLabelText("API key"), { target: { value: "k" } });
        // Token and SID still empty: not sendable.
        expect(
            (screen.getByRole("button", { name: "Connect Exotel" }) as HTMLButtonElement).disabled,
        ).toBe(true);
    });

    it("says in words why a connection was refused", async () => {
        api.connect.mockResolvedValue({
            data: undefined,
            error: { detail: "Smartflo refused the API token -- it may have expired." },
            response: { status: 400 },
        });
        render(<DialerScreen />);
        await screen.findByText("Connect your dialer");
        fireEvent.click(screen.getByRole("radio", { name: "Tata Smartflo" }));
        fireEvent.change(screen.getByLabelText("API token"), { target: { value: "old" } });
        fireEvent.click(screen.getByLabelText(DIALER_CONSENT_CHECKBOX));
        fireEvent.click(screen.getByRole("button", { name: "Connect Tata Smartflo" }));
        expect((await screen.findByRole("alert")).textContent).toMatch(/may have expired/);
    });
});

describe("a connected dialer", () => {
    it("says when last night's import stopped, and what to do", async () => {
        api.list.mockResolvedValue(
            ok({
                connections: [
                    {
                        id: 7,
                        vendor: "smartflo",
                        label: "Sales team",
                        key_last_four: "1234",
                        status: "needs_attention",
                        last_error: "Generate a new token in the Smartflo portal and connect again.",
                        last_synced_at: null,
                    },
                ],
            }),
        );
        api.calls.mockResolvedValue(ok({ calls: [{}, {}, {}] }));
        render(<DialerScreen />);
        expect(await screen.findByText("Needs attention")).toBeTruthy();
        expect(screen.getByText(/Generate a new token/)).toBeTruthy();
        expect(screen.getByText("3 calls imported in the last 7 days.")).toBeTruthy();
        expect(screen.getByText(/key ending 1234/)).toBeTruthy();
    });

    it("disconnects only after saying everything imported is deleted", async () => {
        api.list.mockResolvedValue(
            ok({
                connections: [
                    { id: 7, vendor: "exotel", label: null, key_last_four: "9876", status: "connected", last_error: null, last_synced_at: null },
                ],
            }),
        );
        render(<DialerScreen />);
        fireEvent.click(await screen.findByRole("button", { name: "Disconnect Exotel" }));
        await waitFor(() => expect(api.disconnect).toHaveBeenCalledWith({ path: { connection_id: 7 } }));
        const request = api.confirm.mock.calls[0][0];
        expect(request.destructive).toBe(true);
        expect(request.description).toMatch(/deleted/);
    });
});

it("stands down while the import is switched off", async () => {
    api.list.mockResolvedValue({ data: undefined, error: { detail: "Not Found" }, response: { status: 404 } });
    render(<DialerScreen />);
    expect(await screen.findByText("Connecting a dialer is not available yet")).toBeTruthy();
    expect(screen.queryByText("Connect your dialer")).toBeNull();
});
