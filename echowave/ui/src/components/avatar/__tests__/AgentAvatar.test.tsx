import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NOTIF_BLUE } from "@/lib/bloub/decor";

const put = vi.fn();
vi.mock("@/client/sdk.gen", () => ({
    setWorkflowAvatarApiV1WorkflowWorkflowIdAvatarPut: (...args: unknown[]) => put(...args),
}));

import { AgentAvatar } from "../AgentAvatar";
import { AvatarCustomizer } from "../AvatarCustomizer";

describe("AgentAvatar", () => {
    it("draws a body with eyes cut out of it, in the chosen colour", () => {
        const { container } = render(
            <AgentAvatar avatar={{ shape: "hexagone", color: "violet", expression: "heureux" }} animate={false} label="Ava" />,
        );
        expect(screen.getByRole("img", { name: "Ava" })).toBeTruthy();
        const mask = container.querySelector("mask");
        // the body, and two eyes punched out of it in black
        expect(mask?.querySelectorAll('path[fill="#000"]').length).toBe(2);
        expect(container.querySelector('rect[fill="#8b5cf6"]')).toBeTruthy();
    });

    it("is decorative without a label", () => {
        const { container } = render(<AgentAvatar animate={false} />);
        expect(container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    });

    it("wears a notification dot when the agent needs you", () => {
        const { container } = render(<AgentAvatar state="notify" animate={false} />);
        expect(container.querySelector(`circle[fill="${NOTIF_BLUE}"]`)).toBeTruthy();
    });

    it("changes shape with the choice", () => {
        const { container, rerender } = render(<AgentAvatar avatar={{ shape: "cercle" }} animate={false} />);
        const before = container.querySelector("mask path")?.getAttribute("d");
        rerender(<AgentAvatar avatar={{ shape: "triangle" }} animate={false} />);
        expect(container.querySelector("mask path")?.getAttribute("d")).not.toBe(before);
    });
});

describe("AvatarCustomizer", () => {
    beforeEach(() => put.mockReset());

    const mount = (onSaved = vi.fn()) => {
        render(
            <AvatarCustomizer workflowId={7} name="Ava" avatar={null} open onOpenChange={() => {}} onSaved={onSaved} />,
        );
        return onSaved;
    };

    it("saves the shape, colour and expression picked", async () => {
        put.mockResolvedValue({ data: { avatar: { shape: "nuage", color: "vert", expression: "curieux" } } });
        const onSaved = mount();

        fireEvent.click(screen.getByRole("button", { name: "Cloud" }));
        fireEvent.click(screen.getByRole("button", { name: "Green" }));
        fireEvent.click(screen.getByRole("button", { name: "Curious" }));
        expect(screen.getByRole("button", { name: "Cloud" }).getAttribute("aria-pressed")).toBe("true");
        await act(async () => {
            fireEvent.click(screen.getByRole("button", { name: "Save face" }));
        });

        expect(put).toHaveBeenCalledWith({
            path: { workflow_id: 7 },
            body: { avatar: { shape: "nuage", color: "vert", expression: "curieux" } },
        });
        expect(onSaved).toHaveBeenCalledWith({ shape: "nuage", color: "vert", expression: "curieux" });
    });

    it("resets to the default face", async () => {
        put.mockResolvedValue({ data: { avatar: null } });
        const onSaved = mount();
        await act(async () => {
            fireEvent.click(screen.getByRole("button", { name: "Reset to default" }));
        });
        expect(put).toHaveBeenCalledWith({ path: { workflow_id: 7 }, body: { avatar: null } });
        expect(onSaved).toHaveBeenCalledWith(null);
    });

    it("says so when the save fails, and keeps the dialog open", async () => {
        put.mockResolvedValue({ error: { detail: "Workflow with id 7 not found" } });
        const onSaved = mount();
        await act(async () => {
            fireEvent.click(screen.getByRole("button", { name: "Save face" }));
        });
        expect(screen.getByRole("alert").textContent).toBe("Workflow with id 7 not found");
        expect(onSaved).not.toHaveBeenCalled();
    });
});
