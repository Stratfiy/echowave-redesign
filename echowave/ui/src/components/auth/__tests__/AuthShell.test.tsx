import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import { AuthShell } from "../AuthShell";

describe("the door a stranger arrives at", () => {
    it("leads with the founder's positioning, not one channel", () => {
        // Decided 9 Oct 2026: Decibyl is an intelligent agent that grows and
        // evolves with you -- one personal assistant for life and work. It
        // used to read "An agent for every job nobody has time for" over a
        // pitch about answering the phone.
        render(
            <AuthShell>
                <p>form</p>
            </AuthShell>,
        );
        expect(screen.getByRole("heading").textContent).toMatch(
            /An intelligent agent that grows and evolves with you/,
        );
        const pitch = screen.getByText(/personal assistant for life and work/i);
        expect(pitch.textContent).toMatch(/give it a task/i);
        expect(pitch.textContent).toMatch(/follow through/i);
        expect(pitch.textContent).toMatch(/with your approval/i);
        expect(pitch.textContent).not.toMatch(/answer the phone/i);
    });

    it("carries none of the mauve the product dropped", () => {
        // The orb, the tinted byline chip and the accented half-sentence were
        // all the same colour the chat lost. An accent that names no product
        // idea is colour for its own sake.
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
        const pitch = screen.getByText(/personal assistant for life and work/i);
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
