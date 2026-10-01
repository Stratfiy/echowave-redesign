/**
 * A bot's face is its job, in a colour that does not move.
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

    it("draws the job as a 3D picture when asked, with the shell on", () => {
        flags.shell = true;
        const { container } = render(<BotAvatar id={1} name="Payment reminders" size="md" art />);
        const img = container.querySelector("img");
        expect(img?.getAttribute("src")).toBe("/art/3d/wallet.webp");
        expect(img?.getAttribute("alt")).toBe("");
        expect(container.querySelector("svg")).toBeNull();
    });

    it("keeps the icon when not asked for art", () => {
        flags.shell = true;
        const { container } = render(<BotAvatar id={1} name="Payment reminders" />);
        expect(container.querySelector("img")).toBeNull();
    });
});
