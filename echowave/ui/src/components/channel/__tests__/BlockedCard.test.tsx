/**
 * A bot stopped, and this is what to do about it.
 *
 * These rows were an amber icon and a sentence. "Could not finish its run"
 * is a symptom, and the reader was left to work out whether that meant no
 * credit, a dead connector, or a missing field — three problems with three
 * different answers, one of which takes a minute to fix.
 *
 * Guarded here: the card renders what the server named and invents nothing
 * (a made-up choice is a door that is not there), and it survives a wall
 * with no ways rather than rendering an empty list.
 */

import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import { BlockedCard } from "../BlockedCard";

const NO_CREDIT = {
    reason: "no_quota",
    says: "It ran out of credit part-way through, so it stopped rather than half-finish.",
    ways: [
        { letter: "A", label: "Top up now", href: "/billing" },
        { letter: "B", label: "Turn on automatic top-up", href: "/billing" },
        { letter: "C", label: "See what it was doing", href: "/workflow/7/runs" },
    ],
};

describe("BlockedCard", () => {
    it("keeps the bot's own words above the explanation", () => {
        render(<BlockedCard wall={NO_CREDIT} summary="Morning summary could not run" />);
        expect(screen.getByText("Morning summary could not run")).toBeTruthy();
    });

    it("names the wall rather than the symptom", () => {
        render(<BlockedCard wall={NO_CREDIT} summary="x" />);
        expect(screen.getByText(/ran out of credit/)).toBeTruthy();
    });

    it("offers the ways as lettered links", () => {
        render(<BlockedCard wall={NO_CREDIT} summary="x" />);
        const link = screen.getByRole("link", { name: /Top up now/ });
        expect(link.getAttribute("href")).toBe("/billing");
        expect(screen.getByText("A")).toBeTruthy();
        expect(screen.getByText("C")).toBeTruthy();
    });

    it("renders exactly the ways the server sent, no more", () => {
        render(<BlockedCard wall={NO_CREDIT} summary="x" />);
        expect(screen.getAllByRole("link")).toHaveLength(3);
    });

    it("carries the reason so a wall can be recognised without reading prose", () => {
        render(<BlockedCard wall={NO_CREDIT} summary="x" />);
        expect(
            screen.getByTestId("blocked-card").getAttribute("data-reason"),
        ).toBe("no_quota");
    });

    it("survives a wall with no ways rather than drawing an empty list", () => {
        render(
            <BlockedCard
                wall={{ reason: "unknown", says: "Something stopped it.", ways: [] }}
                summary="x"
            />,
        );
        expect(screen.getByText("Something stopped it.")).toBeTruthy();
        expect(screen.queryAllByRole("link")).toHaveLength(0);
    });

    it("survives a wall whose ways are absent entirely", () => {
        // The generated type makes `ways` optional; an older server that
        // omits it must not blank the card.
        render(
            <BlockedCard wall={{ reason: "unknown", says: "Stopped." }} summary="x" />,
        );
        expect(screen.getByText("Stopped.")).toBeTruthy();
    });
});
