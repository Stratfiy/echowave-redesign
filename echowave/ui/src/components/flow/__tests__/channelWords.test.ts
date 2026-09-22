import { describe, expect, it } from "vitest";

import { channelOf, nodeKindLabel } from "../channelWords";

describe("a node's kind, in the channel's words", () => {
    it("keeps the spec's own words on a phone", () => {
        expect(nodeKindLabel("startCall", "Start Call", "voice")).toBe("Start Call");
        expect(nodeKindLabel("endCall", "End Call", "voice")).toBe("End Call");
        expect(nodeKindLabel("qa", "QA Analysis", "voice")).toBe("QA Analysis");
    });
    it("does not talk about calls off the phone", () => {
        expect(nodeKindLabel("startCall", "Start Call", "chat")).toBe("Start");
        expect(nodeKindLabel("agentNode", "Agent Node", "chat")).toBe("Step");
        expect(nodeKindLabel("endCall", "End Call", "chat")).toBe("Finish");
        expect(nodeKindLabel("qa", "QA Analysis", "chat")).toBe("Review");
    });
    it("passes a channel-neutral type through untouched", () => {
        expect(nodeKindLabel("webhook", "Webhook", "chat")).toBe("Webhook");
        expect(nodeKindLabel(undefined, undefined, "chat")).toBe("Node");
    });
    it("reads an absent channel as voice, like the API", () => {
        expect(channelOf(null)).toBe("voice");
        expect(channelOf({})).toBe("voice");
        expect(channelOf({ channel: "chat" })).toBe("chat");
        expect(channelOf({ channel: "voice" })).toBe("voice");
    });
});
