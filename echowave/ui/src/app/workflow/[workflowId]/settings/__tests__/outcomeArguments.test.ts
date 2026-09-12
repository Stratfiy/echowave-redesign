import { describe, expect, it } from "vitest";

import { toolParameters } from "../outcomeArguments";

/**
 * What an after-call step asks the operator to fill in.
 *
 * A tool already knows its own parameters, so the editor renders a named field
 * with its description rather than a key/value grid. The reading has to be
 * defensive: `definition` is a free-form JSON column that has been through the
 * API, a database and whatever an operator typed into the tool builder.
 */
describe("toolParameters", () => {
    it("reads the fields a tool declares", () => {
        const parameters = toolParameters({
            type: "composio",
            config: {
                parameters: [
                    { name: "recipient_email", description: "Who to send it to", required: true },
                    { name: "subject" },
                ],
            },
        });
        expect(parameters.map((p) => p.name)).toEqual(["recipient_email", "subject"]);
        expect(parameters[0].description).toBe("Who to send it to");
    });

    it("falls back to nothing for a tool whose shape is fixed in code", () => {
        // Google Calendar declares no parameters — its shape lives in the
        // backend. Copying that shape here would drift the first time it
        // changed, so the editor offers free-form fields instead.
        expect(toolParameters({ type: "google_calendar" })).toEqual([]);
    });

    it.each([
        null,
        undefined,
        {},
        { config: null },
        { config: {} },
        { config: { parameters: null } },
        { config: { parameters: "not a list" } },
        "not an object",
        42,
    ])("survives a definition shaped like %p", (definition) => {
        expect(toolParameters(definition)).toEqual([]);
    });

    it("skips entries that could not name a field", () => {
        // A half-saved parameter row must not render an input with no name,
        // which would silently write an empty key into the arguments.
        const parameters = toolParameters({
            config: {
                parameters: [{ name: "" }, { description: "orphan" }, null, "x", { name: "ok" }],
            },
        });
        expect(parameters.map((p) => p.name)).toEqual(["ok"]);
    });
});
