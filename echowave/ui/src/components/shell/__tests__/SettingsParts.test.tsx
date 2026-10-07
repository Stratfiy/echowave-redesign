import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConnectionRow } from "../ConnectionRow";
import { SaveBar } from "../SaveBar";
import { SettingsSection } from "../SettingsSection";

describe("SettingsSection", () => {
    it("is a labelled region with a deep-linkable id and its scope", () => {
        render(
            <SettingsSection id="language" title="Language" description="How Decibyl writes to you." scope="Just you">
                <p>controls</p>
            </SettingsSection>,
        );
        const region = screen.getByRole("region", { name: "Language" });
        expect(region.id).toBe("language");
        expect(screen.getByText("Just you")).toBeTruthy();
        expect(screen.getByText("controls")).toBeTruthy();
    });
});

describe("SaveBar", () => {
    it("is absent with nothing to save", () => {
        const { container } = render(<SaveBar state="clean" onSave={vi.fn()} onDiscard={vi.fn()} />);
        expect(container.firstChild).toBeNull();
    });

    it("saves and discards while dirty, and cannot be pressed while saving", () => {
        const onSave = vi.fn();
        const onDiscard = vi.fn();
        const { rerender } = render(<SaveBar state="dirty" onSave={onSave} onDiscard={onDiscard} />);
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        fireEvent.click(screen.getByRole("button", { name: "Discard" }));
        expect(onSave).toHaveBeenCalledOnce();
        expect(onDiscard).toHaveBeenCalledOnce();
        rerender(<SaveBar state="saving" onSave={onSave} onDiscard={onDiscard} />);
        expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    });

    it("keeps the draft on a rejection and says why", () => {
        render(<SaveBar state="rejected" message="Timezone is not valid." onSave={vi.fn()} onDiscard={vi.fn()} />);
        expect(screen.getByRole("status").textContent).toContain("Timezone is not valid.");
        expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
    });

    it("never overwrites silently on a conflict", () => {
        render(<SaveBar state="conflict" onSave={vi.fn()} onDiscard={vi.fn()} />);
        expect(screen.getByRole("status").textContent).toContain("Review both versions");
    });
});

describe("ConnectionRow", () => {
    it("states each capability with its reason and next step", () => {
        const { rerender } = render(
            <ConnectionRow name="Gmail" state="needs_setup" reason="Sign-in expired" action={<button type="button">Reconnect</button>} />,
        );
        const row = screen.getByTestId("connection-row");
        expect(row.textContent).toContain("Needs setup");
        expect(row.textContent).toContain("Sign-in expired");
        expect(screen.getByRole("button", { name: "Reconnect" })).toBeTruthy();
        rerender(<ConnectionRow name="Gmail" state="available" reason="ignored" detail="nithya@clinic.in" />);
        expect(screen.getByTestId("connection-row").textContent).toContain("Connected");
        expect(screen.getByTestId("connection-row").textContent).not.toContain("ignored");
        rerender(<ConnectionRow name="Slack" state="disabled_by_policy" />);
        expect(screen.getByTestId("connection-row").textContent).toContain("Turned off by your workspace");
    });
});
