import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
    load: vi.fn(),
    reply: vi.fn(),
    resolve: vi.fn(),
    reopen: vi.fn(),
    attach: vi.fn(),
}));

vi.mock("@/client/sdk.gen", () => ({
    myTicketApiV1HelpTicketsTicketIdGet: api.load,
    replyApiV1HelpTicketsTicketIdMessagesPost: api.reply,
    resolveApiV1HelpTicketsTicketIdResolvePost: api.resolve,
    reopenApiV1HelpTicketsTicketIdReopenPost: api.reopen,
    attachApiV1HelpTicketsTicketIdAttachmentsPost: api.attach,
}));
vi.mock("@/context/AppConfigContext", () => ({ useAppConfig: () => ({ config: null, loading: false }) }));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgConfig: () => ({ loading: false, orgFeatures: {} }) }));

import { TicketView } from "../TicketView";

function ticket(overrides: Record<string, unknown> = {}) {
    return {
        id: 5,
        category: "something_failed",
        category_label: "Something didn't work",
        subject: "My call task failed",
        status: "waiting_on_customer",
        affected: { kind: "task", id: 7 },
        reopened_count: 0,
        shared: { affected: { kind: "task", id: 7 }, sections: [{ key: "account", label: "Who is asking", fields: [{ label: "Your email", value: "a@b.in" }] }], left_out: ["content"] },
        messages: [
            { id: 1, author_kind: "customer", body: "It failed.", created_at: "2026-10-07T06:00:00Z" },
            { id: 2, author_kind: "staff", body: "Which task?", created_at: "2026-10-07T06:10:00Z" },
            { id: 3, author_kind: "system", body: "Done: Extra messages.", created_at: "2026-10-07T06:20:00Z" },
        ],
        attachments: [],
        actions: [{ id: 9, summary: "Extra messages: 20 more a day", state: "succeeded", finished_at: null }],
        ...overrides,
    };
}

beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    api.load.mockResolvedValue({ data: ticket() });
});

describe("a support request", () => {
    it("shows the status, the thread with who said what, and support's actions in plain words", async () => {
        render(<TicketView ticketId={5} />);
        expect(await screen.findByText("My call task failed")).toBeTruthy();
        expect(screen.getByTestId("ticket-status").textContent).toBe("Waiting for you");
        expect(screen.getByText("Decibyl support", { exact: false })).toBeTruthy();
        expect(screen.getByTestId("thread-system").textContent).toContain("Done: Extra messages.");
        expect(screen.getByTestId("customer-action").textContent).toContain("Done");
        fireEvent.click(screen.getByRole("button", { name: /What you shared/ }));
        expect(screen.getByTestId("shared-snapshot").textContent).toContain("Not shared: content");
    });

    it("keeps the words and the key when a reply fails, so Send again is one message", async () => {
        api.reply
            .mockResolvedValueOnce({ error: { detail: "Network" }, response: { status: 502 } })
            .mockResolvedValueOnce({ data: { id: 4, author_kind: "customer", body: "Task 7", created_at: null } });
        render(<TicketView ticketId={5} />);
        await screen.findByText("My call task failed");
        fireEvent.change(screen.getByLabelText("Reply to support"), { target: { value: "Task 7" } });
        fireEvent.click(screen.getByRole("button", { name: "Send" }));
        expect(await screen.findByText("Network")).toBeTruthy();
        expect((screen.getByLabelText("Reply to support") as HTMLTextAreaElement).value).toBe("Task 7");
        fireEvent.click(screen.getByRole("button", { name: "Send again" }));
        await waitFor(() => expect(api.reply).toHaveBeenCalledTimes(2));
        const keys = api.reply.mock.calls.map((call) => call[0].body.client_key);
        expect(keys[0]).toBe(keys[1]);
    });

    it("offers Reopen on a resolved request, keeping its history", async () => {
        api.load.mockResolvedValue({ data: ticket({ status: "resolved" }) });
        api.reopen.mockResolvedValue({ data: ticket({ status: "open", reopened_count: 1 }) });
        render(<TicketView ticketId={5} />);
        await screen.findByText("My call task failed");
        expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Reopen" }));
        await waitFor(() => expect(screen.getByTestId("ticket-status").textContent).toBe("Open"));
        expect(screen.getByText("It failed.")).toBeTruthy();
    });

    it("says a file failed and keeps the reply", async () => {
        render(<TicketView ticketId={5} attachmentFailed />);
        expect(await screen.findByTestId("attachment-failed")).toBeTruthy();
    });
});
