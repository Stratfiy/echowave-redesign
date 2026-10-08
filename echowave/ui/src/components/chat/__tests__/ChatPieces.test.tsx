import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AttachMenu } from "../AttachMenu";
import { PasteNotesDialog } from "../PasteNotesDialog";

describe("AttachMenu", () => {
    it("calls each item's own action", async () => {
        const handlers = { onFiles: vi.fn(), onVoiceNote: vi.fn(), onPasteNotes: vi.fn(), onMeetingMode: vi.fn() };
        render(<AttachMenu {...handlers} meetingAvailable />);
        fireEvent.keyDown(screen.getByRole("button", { name: "Attach" }), { key: "Enter" });
        fireEvent.click(await screen.findByText("Voice note"));
        expect(handlers.onVoiceNote).toHaveBeenCalledOnce();
        fireEvent.keyDown(screen.getByRole("button", { name: "Attach" }), { key: "Enter" });
        fireEvent.click(await screen.findByText("Files"));
        expect(handlers.onFiles).toHaveBeenCalledOnce();
    });

    it("shows meeting mode as not available until it is", async () => {
        render(<AttachMenu onFiles={vi.fn()} onVoiceNote={vi.fn()} onPasteNotes={vi.fn()} onMeetingMode={vi.fn()} meetingAvailable={false} />);
        fireEvent.keyDown(screen.getByRole("button", { name: "Attach" }), { key: "Enter" });
        const item = (await screen.findByText("Meeting mode")).closest("[role=menuitem]");
        expect(item?.getAttribute("aria-disabled")).toBe("true");
        expect(screen.getByText("Not available yet")).toBeTruthy();
    });
});

describe("PasteNotesDialog", () => {
    it("adds trimmed notes and closes; empty notes cannot be added", () => {
        const onAdd = vi.fn();
        const onOpenChange = vi.fn();
        render(<PasteNotesDialog open onOpenChange={onOpenChange} onAdd={onAdd} />);
        const add = screen.getByRole("button", { name: "Add notes" }) as HTMLButtonElement;
        expect(add.disabled).toBe(true);
        fireEvent.change(screen.getByLabelText("Notes"), { target: { value: "  call Ravi  " } });
        fireEvent.click(add);
        expect(onAdd).toHaveBeenCalledWith("call Ravi");
        expect(onOpenChange).toHaveBeenCalledWith(false);
    });
});
