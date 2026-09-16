/**
 * Which door a new bot comes through.
 *
 * The button used to open a menu of two: a wizard that asks eleven questions
 * and then writes a flow with a language model, and a blank graph of nodes.
 * Both are the wrong first door, and the ready-made bots -- better than
 * anything written on the spot, open in a second -- were shown only to an
 * account with no bots at all.
 *
 * Guarded: the named bots lead; the other two routes are present *whatever*
 * the catalogue does, because a dialog with no way out of it is worse than
 * the dropdown it replaced.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.hoisted(() => vi.fn());
const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());
const createWorkflow = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh: vi.fn() }) }));
vi.mock("@/client/client.gen", () => ({ client: { get, post } }));
vi.mock("@/client/sdk.gen", () => ({
    createWorkflowApiV1WorkflowCreateDefinitionPost: createWorkflow,
}));
vi.mock("@/lib/auth", () => ({
    useAuth: () => ({ user: { id: 1 }, getAccessToken: async () => "t" }),
}));

import { CreateWorkflowButton } from "../CreateWorkflowButton";

const TEMPLATES = {
    templates: [
        {
            id: "dental",
            name: "Dental clinic front desk",
            vertical: "healthcare",
            direction: "inbound",
            summary: "Books appointments and answers hours",
            languages: ["en", "hi"],
        },
    ],
};

beforeEach(() => {
    push.mockReset();
    get.mockReset();
    post.mockReset();
    createWorkflow.mockReset();
    get.mockResolvedValue({ data: TEMPLATES, error: undefined });
});

async function openDialog() {
    render(<CreateWorkflowButton />);
    fireEvent.click(screen.getByRole("button", { name: /New bot/ }));
}

describe("the new-bot picker", () => {
    it("leads with the ready-made bots, named by the business they are for", async () => {
        await openDialog();
        expect(await screen.findByText("Dental clinic front desk")).toBeTruthy();
    });

    it("still offers describing it and an empty canvas", async () => {
        await openDialog();
        expect(await screen.findByText("Describe what you want instead")).toBeTruthy();
        expect(screen.getByText("Start from an empty canvas")).toBeTruthy();
    });

    it("keeps both of those when the catalogue is empty", async () => {
        // StartFromTemplate renders nothing here. A dialog with no way out of
        // it would be worse than the dropdown this replaced.
        get.mockResolvedValue({ data: { templates: [] }, error: undefined });
        await openDialog();
        expect(await screen.findByText("Describe what you want instead")).toBeTruthy();
        expect(screen.getByText("Start from an empty canvas")).toBeTruthy();
    });

    it("keeps both of those when the catalogue cannot be reached", async () => {
        get.mockResolvedValue({ data: undefined, error: { detail: "down" } });
        await openDialog();
        expect(await screen.findByText("Describe what you want instead")).toBeTruthy();
    });

    it("sends describing it to the wizard", async () => {
        await openDialog();
        fireEvent.click(await screen.findByText("Describe what you want instead"));
        await waitFor(() => expect(push).toHaveBeenCalledWith("/workflow/create"));
    });

    it("opens the bot a template made, rather than dropping the reader back on the list", async () => {
        post.mockResolvedValue({ data: { id: 42 }, error: undefined });
        await openDialog();
        fireEvent.click(await screen.findByText("Dental clinic front desk"));
        await waitFor(() => expect(push).toHaveBeenCalledWith("/workflow/42"));
    });

    it("creates and opens an empty canvas", async () => {
        createWorkflow.mockResolvedValue({ data: { id: 7 } });
        await openDialog();
        fireEvent.click(await screen.findByText("Start from an empty canvas"));
        await waitFor(() => expect(push).toHaveBeenCalledWith("/workflow/7"));
    });

    it("does not say Create Bot with a chevron any more", async () => {
        render(<CreateWorkflowButton />);
        expect(screen.queryByText("Create Bot")).toBeNull();
        expect(screen.getByRole("button", { name: /New bot/ })).toBeTruthy();
    });
});
