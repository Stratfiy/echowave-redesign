import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
    options: vi.fn(),
    preview: vi.fn(),
    create: vi.fn(),
    attach: vi.fn(),
}));
const router = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("@/client/sdk.gen", () => ({
    helpOptionsApiV1HelpOptionsGet: api.options,
    sharePreviewApiV1HelpSharePreviewPost: api.preview,
    createTicketApiV1HelpTicketsPost: api.create,
    attachApiV1HelpTicketsTicketIdAttachmentsPost: api.attach,
}));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/context/AppConfigContext", () => ({ useAppConfig: () => ({ config: null, loading: false }) }));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgConfig: () => ({ loading: false, orgFeatures: {} }) }));

import { HelpRequestForm } from "../HelpRequestForm";

function previewFor(share: string[] | null) {
    const on = new Set(share ?? ["task_metadata"]);
    return {
        data: {
            affected: { kind: "task", id: 7 },
            sections: [
                { key: "account", label: "Who is asking", included: true, required: true, fields: [] },
                { key: "task_metadata", label: "Details", included: on.has("task_metadata"), required: false, fields: [{ label: "State", value: "failed" }] },
                { key: "content", label: "The words themselves", included: on.has("content"), required: false, fields: [{ label: "Title", value: "Call Dr Rao" }] },
            ],
            not_shared: "Not shared: recordings.",
        },
    };
}

beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    router.push.mockReset();
    api.options.mockResolvedValue({
        data: {
            categories: [{ key: "something_failed", label: "Something didn't work" }],
            not_shared: "x",
            max_attachment_bytes: 5 * 1024 * 1024,
            attachment_types: ["image/png"],
        },
    });
    api.preview.mockImplementation(async ({ body }: { body: { share: string[] | null } }) => previewFor(body.share));
});

async function fill() {
    await screen.findByTestId("share-preview");
    fireEvent.change(screen.getByLabelText("What happened?"), { target: { value: "My call task failed." } });
}

describe("asking support about a failed task", () => {
    it("previews the default share, then the words only when switched on", async () => {
        render(<HelpRequestForm affectedKind="task" affectedId={7} />);
        await screen.findByTestId("share-preview");
        expect(api.preview.mock.calls[0][0].body).toEqual({ affected_kind: "task", affected_id: 7, share: null });
        expect(screen.getByText("About task #7")).toBeTruthy();
        fireEvent.click(screen.getByLabelText(/The words themselves/));
        await waitFor(() => expect(api.preview).toHaveBeenCalledTimes(2));
        expect(api.preview.mock.calls[1][0].body.share).toEqual(["task_metadata", "content"]);
    });

    it("sends once per draft: a retry after a failure carries the same key", async () => {
        api.create
            .mockResolvedValueOnce({ error: { detail: "Server busy" }, response: { status: 503 } })
            .mockResolvedValueOnce({ data: { ticket: { id: 12 }, created: true } });
        render(<HelpRequestForm affectedKind="task" affectedId={7} />);
        await fill();
        fireEvent.click(screen.getByRole("button", { name: "Send to support" }));
        expect(await screen.findByText("Server busy")).toBeTruthy();
        // The words are kept.
        expect((screen.getByLabelText("What happened?") as HTMLTextAreaElement).value).toBe("My call task failed.");
        fireEvent.click(screen.getByRole("button", { name: "Send to support" }));
        await waitFor(() => expect(router.push).toHaveBeenCalledWith("/help/12"));
        const keys = api.create.mock.calls.map((call) => call[0].headers["Idempotency-Key"]);
        expect(keys[0]).toBeTruthy();
        expect(keys[0]).toBe(keys[1]);
        expect(api.create.mock.calls[1][0].body).toMatchObject({
            category: "something_failed",
            affected_kind: "task",
            affected_id: 7,
            share: null,
        });
    });

    it("keeps the request when the file cannot be stored, and says so", async () => {
        api.create.mockResolvedValue({ data: { ticket: { id: 13 }, created: true } });
        api.attach.mockResolvedValue({ error: { detail: "not stored" }, response: { status: 503 } });
        render(<HelpRequestForm affectedKind="task" affectedId={7} />);
        await fill();
        const file = new File(["png"], "shot.png", { type: "image/png" });
        fireEvent.change(screen.getByTestId("help-file"), { target: { files: [file] } });
        expect(await screen.findByText("shot.png")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Send to support" }));
        await waitFor(() => expect(router.push).toHaveBeenCalledWith("/help/13?attachment=failed"));
    });

    it("sends exactly what the preview shows when two switches are flipped quickly", async () => {
        // The first change's preview answers last. What the person reads on
        // the panel and what is sent must still be the same thing.
        let releaseFirst: () => void = () => {};
        api.preview.mockImplementationOnce(async ({ body }: { body: { share: string[] | null } }) => previewFor(body.share));
        api.preview.mockImplementationOnce(
            ({ body }: { body: { share: string[] | null } }) =>
                new Promise((resolve) => {
                    releaseFirst = () => resolve(previewFor(body.share));
                }),
        );
        api.create.mockResolvedValue({ data: { ticket: { id: 14 }, created: true } });
        render(<HelpRequestForm affectedKind="task" affectedId={7} />);
        await fill();
        fireEvent.click(screen.getByLabelText(/Details/)); // details off: answers late
        fireEvent.click(screen.getByLabelText(/The words themselves/)); // words on
        await waitFor(() => expect(api.preview).toHaveBeenCalledTimes(3));
        await waitFor(() => expect(screen.getByTestId("share-content").getAttribute("data-included")).toBe("true"));
        releaseFirst();
        await new Promise((r) => setTimeout(r, 0));
        const shown = ["task_metadata", "content"].filter((k) => screen.getByTestId(`share-${k}`).getAttribute("data-included") === "true");
        fireEvent.click(screen.getByRole("button", { name: "Send to support" }));
        await waitFor(() => expect(api.create).toHaveBeenCalled());
        expect(api.create.mock.calls[0][0].body.share).toEqual(shown);
        expect(shown).toEqual(["content"]);
    });

    it("does not send while what would be shared cannot be shown", async () => {
        render(<HelpRequestForm affectedKind="task" affectedId={7} />);
        await fill();
        api.preview.mockResolvedValueOnce({ error: { detail: "Server busy" }, response: { status: 503 } });
        fireEvent.click(screen.getByLabelText(/The words themselves/));
        expect(await screen.findByText("Could not show what will be shared.")).toBeTruthy();
        expect((screen.getByRole("button", { name: "Send to support" }) as HTMLButtonElement).disabled).toBe(true);
    });

    it("refuses a file type support cannot take, before sending", async () => {
        render(<HelpRequestForm />);
        await screen.findByTestId("share-preview");
        const file = new File(["#!"], "run.sh", { type: "application/x-sh" });
        fireEvent.change(screen.getByTestId("help-file"), { target: { files: [file] } });
        expect(await screen.findByText("Attach a screenshot, a PDF or a text file.")).toBeTruthy();
    });
});
