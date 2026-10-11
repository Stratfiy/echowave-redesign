/**
 * "Can see the team" is what lets a chief-of-staff agent read the rest of
 * the workspace, so what these hold: it is absent while the feature is off,
 * it starts off, it saves on the tick, and a refused save puts the switch
 * back rather than leaving a lie on screen.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const read = vi.hoisted(() => vi.fn());
const write = vi.hoisted(() => vi.fn());
const feature = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    getTeamAccessApiV1WorkflowWorkflowIdTeamAccessGet: read,
    setTeamAccessApiV1WorkflowWorkflowIdTeamAccessPut: write,
}));
vi.mock("@/lib/features", () => ({ useFeature: feature }));

import { TeamAccessCard } from "../TeamAccessCard";

beforeEach(() => {
    read.mockReset();
    write.mockReset();
    feature.mockReset();
    feature.mockReturnValue(true);
    read.mockResolvedValue({ data: { enabled: false } });
    write.mockResolvedValue({ data: { enabled: true } });
});

describe("Can see the team", () => {
    it("is absent, and asks nothing, while the feature is off", () => {
        feature.mockReturnValue(false);
        const { container } = render(<TeamAccessCard workflowId={35} />);
        expect(container.innerHTML).toBe("");
        expect(read).not.toHaveBeenCalled();
        expect(feature).toHaveBeenCalledWith("team_activity");
    });

    it("starts off for an agent nobody has switched", async () => {
        render(<TeamAccessCard workflowId={35} />);
        const toggle = await screen.findByRole("switch", { name: "Can see the team" });
        expect(toggle.getAttribute("data-state")).toBe("unchecked");
        expect(read).toHaveBeenCalledWith({ path: { workflow_id: 35 } });
    });

    it("shows it on when the owner already turned it on", async () => {
        read.mockResolvedValue({ data: { enabled: true } });
        render(<TeamAccessCard workflowId={35} />);
        const toggle = await screen.findByRole("switch", { name: "Can see the team" });
        expect(toggle.getAttribute("data-state")).toBe("checked");
    });

    it("saves on the tick", async () => {
        render(<TeamAccessCard workflowId={35} />);
        fireEvent.click(await screen.findByRole("switch", { name: "Can see the team" }));
        await waitFor(() => expect(write).toHaveBeenCalled());
        expect(write.mock.calls[0][0]).toEqual({
            path: { workflow_id: 35 },
            body: { enabled: true },
        });
    });

    it("puts the switch back when the save is refused", async () => {
        write.mockResolvedValue({ error: { detail: "nope" } });
        render(<TeamAccessCard workflowId={35} />);
        const toggle = await screen.findByRole("switch", { name: "Can see the team" });
        fireEvent.click(toggle);
        expect(await screen.findByRole("alert")).toBeTruthy();
        await waitFor(() =>
            expect(
                screen.getByRole("switch", { name: "Can see the team" }).getAttribute("data-state"),
            ).toBe("unchecked"),
        );
    });
});
