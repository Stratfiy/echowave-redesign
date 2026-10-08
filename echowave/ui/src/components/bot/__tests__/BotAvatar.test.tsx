/**
 * A bot's face: its blob, from its id, so a rename does not repaint it.
 * The job icon and tone helpers are still exported and tested.
 */

import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const flags = vi.hoisted(() => ({ shell: false }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => name === "shell" && flags.shell }));

import { BotAvatar, botIcon, botTone } from "../BotAvatar";

afterEach(() => {
    cleanup();
    flags.shell = false;
});

describe("an agent's face", () => {
    it("reads the job out of the name", () => {
        // A name in this product is almost always the job.
        expect(botIcon("Front desk").displayName).toBe("Headset");
        expect(botIcon("Quote desk").displayName).toBe("FileText");
        expect(botIcon("Payment reminders").displayName).toBe("Wallet");
        expect(botIcon("Narayani Dental Clinic").displayName).toBe("Stethoscope");
        expect(botIcon("Appointment reminders").displayName).toBe("CalendarCheck");
    });

    it("says so plainly when the name says nothing", () => {
        // Honest: we do not know what "Bot 3" does either.
        // The lucide icon's own name, not copy.
        expect(botIcon("Agent 3").displayName).toBe("Bot");
        expect(botIcon("").displayName).toBe("Bot");
    });

    it("takes its colour from the id, so a rename does not repaint it", () => {
        expect(botTone(7)).toBe(botTone(7));
        expect(botTone(7)).not.toBe(botTone(8));
        // Negative and string ids still land on a real tone.
        expect(botTone(-3)).toBeTruthy();
        expect(botTone("abc")).toBeTruthy();
    });

    it("draws a decoration, not something a screen reader reads out", () => {
        // The name is beside it in every place this is used; a second copy
        // in the accessibility tree is noise.
        const { container } = render(<BotAvatar id={1} name="Front desk" />);
        expect(container.querySelector('[aria-hidden="true"]')).toBeTruthy();
        expect(screen.queryByText("Front desk")).toBeNull();
    });

    it("draws the agent's blob, the same one for the same id", () => {
        const first = render(<BotAvatar id={7} name="Payment reminders" size="md" />);
        const face = first.container.querySelector('[data-testid="blob-face"]');
        expect(face?.getAttribute("aria-hidden")).toBe("true");
        expect(face?.getAttribute("width")).toBe("32");
        const fill = face?.querySelector("path")?.getAttribute("fill");
        cleanup();
        // Renamed, same id: same face.
        const again = render(<BotAvatar id={7} name="Collections" size="md" />);
        expect(again.container.querySelector('[data-testid="blob-face"] path')?.getAttribute("fill")).toBe(fill);
        expect(again.container.querySelector("img")).toBeNull();
    });
});
