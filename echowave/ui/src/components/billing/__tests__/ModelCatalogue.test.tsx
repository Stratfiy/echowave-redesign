import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ModelCatalogue } from "../ModelCatalogue";

const api = { get: vi.fn(), put: vi.fn() };
vi.mock("@/client/client.gen", () => ({
    client: {
        get: (...args: unknown[]) => api.get(...args),
        put: (...args: unknown[]) => api.put(...args),
    },
}));

const FOUND = {
    provider: "anthropic",
    component: "llm",
    source: "catalogue",
    note: null,
    has_key: true,
    allow_custom_model: true,
    models: [
        { id: "claude-haiku-4-5", suggested: true, offered: false },
        { id: "claude-sonnet-5", suggested: true, offered: true },
        { id: "claude-opus-5", suggested: true, offered: false },
    ],
};

beforeEach(() => {
    api.get.mockReset();
    api.put.mockReset();
    api.get.mockResolvedValue({ data: FOUND, error: undefined });
    api.put.mockResolvedValue({ data: { models: [] }, error: undefined });
});

function catalogue() {
    return <ModelCatalogue component="llm" provider="anthropic" onSaved={() => {}} />;
}

describe("the model id is what identifies the row", () => {
    it("shows every model's id", async () => {
        // An operator on a tablet saw five rows reading only "integrated",
        // with no way to tell which model each one was: the row was a single
        // unwrapping line and the name field's fixed 176px squeezed the id to
        // nothing. The id is the whole point of the row.
        render(catalogue());

        for (const model of FOUND.models) {
            expect(await screen.findByText(model.id)).toBeTruthy();
        }
    });

    it("keeps the id readable on a ticked row, where the name field appears", async () => {
        render(catalogue());
        const already = await screen.findByText("claude-sonnet-5");

        expect(already).toBeTruthy();
        expect(
            screen.getByLabelText("Name customers see for claude-sonnet-5"),
        ).toBeTruthy();
    });

    it("carries the id as a title, so it is recoverable however it is laid out", async () => {
        render(catalogue());
        const id = await screen.findByText("claude-opus-5");

        expect(id.getAttribute("title")).toBe("claude-opus-5");
    });

    it("names the field after its model, so two rows are never confused", async () => {
        render(catalogue());
        await screen.findByText("claude-sonnet-5");

        fireEvent.click(screen.getByRole("button", { name: "claude-haiku-4-5" }));

        expect(
            screen.getByLabelText("Name customers see for claude-haiku-4-5"),
        ).toBeTruthy();
    });
});

describe("ticking a model puts it on sale", () => {
    it("sends exactly the ticked set, not a merge", async () => {
        // A replace, not a merge: an unticked box means "we do not offer this",
        // and merging would make removal impossible from the only screen that
        // writes here.
        render(catalogue());
        await screen.findByText("claude-haiku-4-5");

        fireEvent.click(screen.getByRole("button", { name: "claude-haiku-4-5" }));
        fireEvent.click(screen.getByRole("button", { name: /^Offer \d+ model/ }));

        await waitFor(() => expect(api.put).toHaveBeenCalled());
        const body = api.put.mock.calls[0][0].body;
        expect(body.component).toBe("llm");
        expect(body.provider).toBe("anthropic");
        expect(new Set(body.models)).toEqual(
            new Set(["claude-sonnet-5", "claude-haiku-4-5"]),
        );
    });
});
