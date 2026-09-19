import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import { AuthShell } from "../AuthShell";

describe("the door a stranger arrives at", () => {
    it("pitches the whole product, not the phone alone", () => {
        // It read "Decibyl answers your phone" -- true, and a third of what a
        // customer buys. Somebody who wants a bot that replies on WhatsApp or
        // files what it finished had no way to tell this does that.
        render(
            <AuthShell>
                <p>form</p>
            </AuthShell>,
        );
        const pitch = screen.getByText(/bots answer the phone/i);
        expect(pitch.textContent).toMatch(/WhatsApp/);
        expect(pitch.textContent).toMatch(/hand back what they/i);
        expect(screen.getByRole("heading").textContent).toMatch(/A bot for every job/);
    });

    it("carries none of the mauve the product dropped", () => {
        // The orb, the tinted byline chip and the accented half-sentence were
        // all the same colour the chat lost. An accent that names no product
        // idea -- "nobody has time for" -- is colour for its own sake.
        const { container } = render(
            <AuthShell>
                <p>form</p>
            </AuthShell>,
        );
        expect(container.innerHTML).not.toMatch(/accent-brand/);
    });

    it("keeps the languages, which is the part competitors cannot copy cheaply", () => {
        render(
            <AuthShell>
                <p>form</p>
            </AuthShell>,
        );
        const pitch = screen.getByText(/bots answer the phone/i);
        expect(pitch.textContent).toMatch(/Hindi, Tamil, Telugu/);
    });

    it("stands on the same ground as the signed-in app", () => {
        // The orb was a coral-to-mauve gradient the product itself no longer
        // has anywhere -- the chat lost its purple wash for the same reason.
        const { container } = render(
            <AuthShell>
                <p>form</p>
            </AuthShell>,
        );
        expect(container.innerHTML).not.toMatch(/brand-gradient/);
        expect(container.innerHTML).not.toMatch(/blur-3xl/);
        expect(container.querySelector(".bg-background")).toBeTruthy();
    });
});
