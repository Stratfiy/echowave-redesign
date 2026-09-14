/**
 * The form that takes a key, and the stamp that replaces it.
 *
 * Guarded: secret fields are password inputs, the values go to the server
 * keyed by field, a refusal shows on the card, and once provided the card is
 * a record that carries a hint and never a value.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const provide = vi.hoisted(() => vi.fn());
vi.mock("@/client/sdk.gen", () => ({ provideSecretApiV1TimelineSecretsProvidePost: provide }));

import { SecretCard } from "../SecretCard";

const fields = [
    { key: "header_name", label: "Header name", secret: false, default: "X-API-Key" },
    { key: "api_key", label: "API key", secret: true },
];

const event = (payload: Record<string, unknown>) => ({
    id: 77,
    at: "2026-09-14T00:00:00Z",
    kind: "needs_secret",
    actor: "agent",
    summary: "Needs Razorpay key",
    payload: { name: "Razorpay key", credential_type: "api_key", fields, ...payload },
    is_deliverable: false,
    workflow_id: 3,
    workflow_run_id: null,
    folder_id: 5,
});

beforeEach(() => provide.mockReset());

describe("the form", () => {
    it("masks the secret field and sends the values keyed by field", async () => {
        provide.mockResolvedValue({
            data: event({ provided: { credential_uuid: "u1", credential_name: "Razorpay key", hint: "7f3a" } }),
        });
        const onProvided = vi.fn();
        render(<SecretCard event={event({ why: "to fetch orders" })} onProvided={onProvided} />);
        expect(screen.getByText("to fetch orders")).toBeTruthy();
        const key = screen.getByLabelText("API key") as HTMLInputElement;
        expect(key.type).toBe("password");
        // The header has a default, so only the key is needed.
        expect(screen.getByRole("button", { name: "Add securely" }).hasAttribute("disabled")).toBe(true);
        fireEvent.change(key, { target: { value: "rzp_live_7f3a" } });
        fireEvent.click(screen.getByRole("button", { name: "Add securely" }));
        await waitFor(() => expect(provide).toHaveBeenCalled());
        expect(provide.mock.calls[0][0].body).toEqual({
            event_id: 77,
            values: { api_key: "rzp_live_7f3a" },
        });
        await waitFor(() => expect(onProvided).toHaveBeenCalled());
    });

    it("shows a refusal on the card", async () => {
        provide.mockResolvedValue({ error: { detail: "Already added." } });
        render(<SecretCard event={event({})} />);
        fireEvent.change(screen.getByLabelText("API key"), { target: { value: "abc" } });
        fireEvent.click(screen.getByRole("button", { name: "Add securely" }));
        expect(await screen.findByRole("alert")).toBeTruthy();
        expect(screen.getByRole("alert").textContent).toContain("Already added.");
    });
});

describe("once provided", () => {
    it("is a stamp with a hint and no input", () => {
        render(
            <SecretCard
                event={event({ provided: { credential_uuid: "u1", credential_name: "Razorpay key", hint: "7f3a" } })}
            />,
        );
        expect(screen.queryByLabelText("API key")).toBeNull();
        expect(screen.getByText(/ends in 7f3a/)).toBeTruthy();
        expect(screen.queryByRole("button")).toBeNull();
    });
});
