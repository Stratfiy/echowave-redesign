/**
 * The field somebody pastes their n8n URL into.
 *
 * Guarded: the events offered come from the server rather than being
 * invented here (a checkbox this screen made up is one somebody ticks and
 * then waits forever for); the secret is shown once and said to be the only
 * copy; the server's own refusal reaches the screen, because it is the half
 * that knows a URL points somewhere private; and a test send is offered only
 * once there is somewhere to send it.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getHook = vi.hoisted(() => vi.fn());
const save = vi.hoisted(() => vi.fn());
const test_ = vi.hoisted(() => vi.fn());
const remove = vi.hoisted(() => vi.fn());
const notices = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    getEventWebhookApiV1WorkflowsWorkflowIdEventWebhookGet: getHook,
    saveEventWebhookApiV1WorkflowsWorkflowIdEventWebhookPut: save,
    testEventWebhookApiV1WorkflowsWorkflowIdEventWebhookTestPost: test_,
    deleteEventWebhookApiV1WorkflowsWorkflowIdEventWebhookDelete: remove,
    getBotNoticesApiV1WorkflowWorkflowIdNoticesGet: notices,
}));

import { EventWebhookPanel } from "../EventWebhookPanel";

const OFFERED = [
    {
        kind: "outcome_filed",
        label: "It finished a job",
        when: "when a bot files an outcome",
        default: true,
    },
    {
        kind: "needs_attention",
        label: "It is stuck",
        when: "when a bot needs somebody",
        default: true,
    },
];

beforeEach(() => {
    vi.clearAllMocks();
    notices.mockResolvedValue({ data: { offered: OFFERED, selected: [] } });
    getHook.mockResolvedValue({ data: null });
    save.mockResolvedValue({
        data: {
            url: "https://hooks.example.com/x",
            kinds: [],
            is_active: true,
            sending: ["outcome_filed", "needs_attention"],
            secret: "abc123",
        },
    });
    test_.mockResolvedValue({ data: { sent: true, detail: "Sent." } });
    remove.mockResolvedValue({ data: undefined });
});

describe("the event webhook panel", () => {
    it("offers the events the server says exist, in words", async () => {
        render(<EventWebhookPanel workflowId={7} />);
        expect(await screen.findByText("It finished a job")).toBeTruthy();
        expect(screen.getByText("when a bot needs somebody")).toBeTruthy();
    });

    it("saves the URL and the ticked events", async () => {
        render(<EventWebhookPanel workflowId={7} />);
        fireEvent.change(await screen.findByLabelText("Where to post"), {
            target: { value: "https://hooks.example.com/x" },
        });
        fireEvent.click(screen.getByRole("checkbox", { name: /It is stuck/ }));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() => expect(save).toHaveBeenCalled());
        const body = save.mock.calls[0][0].body;
        expect(body.url).toBe("https://hooks.example.com/x");
        expect(body.kinds).toEqual(["needs_attention"]);
        expect(body.rotate_secret).toBe(false);
    });

    it("shows the secret once and says it is the only copy", async () => {
        render(<EventWebhookPanel workflowId={7} />);
        fireEvent.change(await screen.findByLabelText("Where to post"), {
            target: { value: "https://hooks.example.com/x" },
        });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(await screen.findByText("abc123")).toBeTruthy();
        expect(
            screen.getByText(/only time it is shown/i)
        ).toBeTruthy();
    });

    it("passes the server's refusal through rather than a generic message", async () => {
        save.mockResolvedValue({
            error: { detail: "URL must resolve to a public IP address in SaaS mode" },
            response: { status: 422 },
        });
        render(<EventWebhookPanel workflowId={7} />);
        fireEvent.change(await screen.findByLabelText("Where to post"), {
            target: { value: "http://169.254.169.254/" },
        });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(await screen.findByText(/public IP address/)).toBeTruthy();
    });

    it("offers a test send only once there is somewhere to send it", async () => {
        render(<EventWebhookPanel workflowId={7} />);
        await screen.findByText("It finished a job");
        expect(screen.queryByRole("button", { name: /send a test/i })).toBeNull();

        getHook.mockResolvedValue({
            data: {
                url: "https://hooks.example.com/x",
                kinds: [],
                is_active: true,
                sending: ["outcome_filed"],
            },
        });
        render(<EventWebhookPanel workflowId={7} />);
        const button = await screen.findByRole("button", { name: /send a test/i });
        fireEvent.click(button);
        await waitFor(() => expect(test_).toHaveBeenCalled());
    });

    it("says what an empty list will send, rather than leaving it blank", async () => {
        getHook.mockResolvedValue({
            data: {
                url: "https://hooks.example.com/x",
                kinds: [],
                is_active: true,
                sending: ["outcome_filed", "needs_attention"],
            },
        });
        render(<EventWebhookPanel workflowId={7} />);
        expect(
            await screen.findByText(/outcome_filed, needs_attention/)
        ).toBeTruthy();
    });

    it("asks before it stops sending", async () => {
        const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
        getHook.mockResolvedValue({
            data: {
                url: "https://hooks.example.com/x",
                kinds: [],
                is_active: true,
                sending: [],
            },
        });
        render(<EventWebhookPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: /remove/i }));
        expect(confirm).toHaveBeenCalled();
        expect(remove).not.toHaveBeenCalled();
        confirm.mockRestore();
    });
});
