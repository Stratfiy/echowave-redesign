/**
 * The skill library, opened from one bot.
 *
 * The plus beside a bot's Skills sends somebody here with ?for=<bot>. The
 * shelf is otherwise a shop with no idea who is shopping, so the test that
 * matters is that the bot who sent them is already ticked when the picker
 * opens, and that saving attaches the skill to it without another search.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
    list: vi.fn(),
    workflows: vi.fn(),
    setBots: vi.fn(),
    install: vi.fn(),
    uninstall: vi.fn(),
    read: vi.fn(),
}));
const params = vi.hoisted(() => ({ value: "" }));

vi.mock("@/client/sdk.gen", () => ({
    listSkillsApiV1SkillsGet: api.list,
    getWorkflowsApiV1WorkflowFetchGet: api.workflows,
    setSkillBotsApiV1SkillsBotsPost: api.setBots,
    installSkillApiV1SkillsInstallPost: api.install,
    uninstallSkillApiV1SkillsUninstallPost: api.uninstall,
    readSkillApiV1SkillsSlugGet: api.read,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("next/navigation", () => ({
    useSearchParams: () => new URLSearchParams(params.value),
}));

import { SkillsShelf } from "../SkillsShelf";

const SKILL = {
    slug: "chase-invoice",
    title: "Chase an unpaid invoice",
    description: "What to say on the third reminder.",
    division: "Finance",
    emoji: "📄",
    on_bots: [{ id: 3, name: "Reception" }],
};

beforeEach(() => {
    vi.clearAllMocks();
    params.value = "";
    api.list.mockResolvedValue({
        data: { installed: [], skills: [SKILL], divisions: ["Finance"], attributions: [] },
    });
    api.workflows.mockResolvedValue({
        data: [
            { id: 3, name: "Reception" },
            { id: 7, name: "Collections" },
        ],
    });
    api.setBots.mockResolvedValue({ data: { ok: true } });
});

const openPicker = async () => {
    render(<SkillsShelf query="" />);
    const add = await screen.findByLabelText("Add Chase an unpaid invoice to an agent");
    await waitFor(() => expect(api.workflows).toHaveBeenCalled());
    fireEvent.click(add);
};

describe("SkillsShelf", () => {
    it("ticks only the agents that already have the skill when nobody sent you", async () => {
        await openPicker();
        const reception = await screen.findByRole("checkbox", { name: "Reception" });
        expect(reception.getAttribute("aria-checked")).toBe("true");
        expect(
            screen.getByRole("checkbox", { name: "Collections" }).getAttribute("aria-checked"),
        ).toBe("false");
    });

    it("pre-ticks the agent named by ?for and saves it alongside the rest", async () => {
        params.value = "for=7";
        await openPicker();
        const collections = await screen.findByRole("checkbox", { name: "Collections" });
        expect(collections.getAttribute("aria-checked")).toBe("true");
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        await waitFor(() =>
            expect(api.setBots).toHaveBeenCalledWith({
                body: { slug: "chase-invoice", workflow_ids: [3, 7] },
            }),
        );
    });

    it("does not tick an agent twice when it already has the skill", async () => {
        params.value = "for=3";
        await openPicker();
        fireEvent.click(await screen.findByRole("button", { name: "Save" }));
        await waitFor(() =>
            expect(api.setBots).toHaveBeenCalledWith({
                body: { slug: "chase-invoice", workflow_ids: [3] },
            }),
        );
    });
});
