import { fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { SharePreviewPanel } from "../SharePreviewPanel";

const preview = {
    affected: { kind: "task" as const, id: 7 },
    sections: [
        { key: "account", label: "Who is asking", included: true, required: true, fields: [{ label: "Your email", value: "asha@example.in" }] },
        { key: "task_metadata", label: "Details", included: true, required: false, fields: [{ label: "State", value: "failed" }] },
        { key: "content", label: "The words themselves", included: false, required: false, fields: [{ label: "Title", value: "Call Dr Rao" }] },
    ],
    not_shared: "Not shared: the rest of your conversations, recordings and audio.",
};

describe("what support will see", () => {
    it("lists every section with its exact fields, and says who is always included", () => {
        render(<SharePreviewPanel preview={preview} onToggle={vi.fn()} />);
        const account = screen.getByTestId("share-account");
        expect(within(account).getByText("Always included, so support knows who to answer.")).toBeTruthy();
        expect(within(account).queryByRole("checkbox")).toBeNull();
        // The words are shown so the choice is informed, and marked not shared.
        const content = screen.getByTestId("share-content");
        expect(within(content).getByText("Call Dr Rao")).toBeTruthy();
        expect(within(content).getByText("Not shared")).toBeTruthy();
        expect(content.getAttribute("data-included")).toBe("false");
        expect(screen.getByText(/recordings and audio/)).toBeTruthy();
    });

    it("switches a section on or off", () => {
        const onToggle = vi.fn();
        render(<SharePreviewPanel preview={preview} onToggle={onToggle} />);
        fireEvent.click(within(screen.getByTestId("share-content")).getByRole("checkbox"));
        expect(onToggle).toHaveBeenCalledWith("content", true);
        fireEvent.click(within(screen.getByTestId("share-task_metadata")).getByRole("checkbox"));
        expect(onToggle).toHaveBeenCalledWith("task_metadata", false);
    });
});
