/**
 * The pencil that changes a bot by saying what you want.
 *
 * Two things matter here and neither is the chat itself, which has its own
 * server tests: the chat opens knowing which bot it is changing, and a turn
 * that rewrote the draft is announced rather than silently applied to the
 * form standing beside it.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

const panel = vi.hoisted(() => vi.fn());
vi.mock("@/components/agent-builder/AgentBuilderPanel", () => ({
    AgentBuilderPanel: (props: Record<string, unknown>) => {
        panel(props);
        return <div data-testid="builder" />;
    },
}));

import { ChangeByChat } from "../ChangeByChat";

const open = (onRevised = vi.fn()) => {
    render(<ChangeByChat name="Clinic front desk" onRevised={onRevised} />);
    fireEvent.click(screen.getByRole("button", { name: /Change by chat/ }));
    return onRevised;
};

describe("ChangeByChat", () => {
    it("opens the chat on the bot it was pressed from", () => {
        open();
        expect(screen.getByTestId("builder")).toBeTruthy();
        const props = panel.mock.calls.at(-1)?.[0] as { prefill?: { text: string } };
        expect(props.prefill?.text).toContain("Clinic front desk");
    });

    it("says the live bot is untouched until somebody publishes", () => {
        open();
        expect(screen.getByText(/live bot keeps answering/)).toBeTruthy();
    });

    it("does not offer to build a second bot while changing this one", () => {
        open();
        const props = panel.mock.calls.at(-1)?.[0] as {
            showSuggestions?: boolean;
            heading?: boolean;
        };
        // The openers are for somebody building their first bot, and the
        // panel's own title says "Build an agent by chatting" -- under a
        // sheet headed "Change <bot>" that reads as a second bot.
        expect(props.showSuggestions).toBe(false);
        expect(props.heading).toBe(false);
    });

    it("reports a turn that rewrote the draft, and ignores one that did not", () => {
        const onRevised = open();
        const props = panel.mock.calls.at(-1)?.[0] as {
            onActions: (actions: string[]) => void;
        };

        props.onActions(["list_my_agents", "estimate_agent_cost"]);
        expect(onRevised).not.toHaveBeenCalled();

        props.onActions(["list_my_agents", "revise_agent_prompt"]);
        expect(onRevised).toHaveBeenCalled();
    });
});
