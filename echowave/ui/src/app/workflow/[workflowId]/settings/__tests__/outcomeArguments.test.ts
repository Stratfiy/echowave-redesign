import { describe, expect, it } from "vitest";

import {
    extractionVariables,
    suggestArguments,
    suggestVariableFor,
    toolParameters,
} from "../outcomeArguments";

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

describe("filling an after-call step from what the agent already collects", () => {
    const definition = {
        nodes: [
            {
                data: {
                    extraction_variables: [
                        { name: "customer_name", type: "string" },
                        { name: "phone", type: "string" },
                    ],
                },
            },
            {
                data: {
                    extraction_variables: [
                        { name: "origin_city", type: "string" },
                        { name: "destination_country", type: "string" },
                        // The same variable on a second node: the editor wants
                        // the vocabulary, not the occurrences.
                        { name: "phone", type: "string" },
                    ],
                },
            },
        ],
    };

    it("reads every variable the agent declares, once each", () => {
        expect(extractionVariables(definition)).toEqual([
            "customer_name",
            "phone",
            "origin_city",
            "destination_country",
        ]);
    });

    it("survives a definition that has been through a canvas and a database", () => {
        expect(extractionVariables(null)).toEqual([]);
        expect(extractionVariables({})).toEqual([]);
        expect(extractionVariables({ nodes: "nope" })).toEqual([]);
        expect(extractionVariables({ nodes: [{}, { data: {} }] })).toEqual([]);
        expect(
            extractionVariables({ nodes: [{ data: { extraction_variables: [{}, { name: "  " }] } }] }),
        ).toEqual([]);
    });

    it("matches an exact name whatever the naming style", () => {
        const vars = ["origin_city"];
        expect(suggestVariableFor("origin_city", vars)).toBe("origin_city");
        expect(suggestVariableFor("originCity", vars)).toBe(null);
        expect(suggestVariableFor("origin city", vars)).toBe("origin_city");
    });

    it("matches synonyms, because two people named the same thing", () => {
        expect(suggestVariableFor("contact_name", ["customer_name"])).toBe("customer_name");
        expect(suggestVariableFor("phone", ["mobile"])).toBe("mobile");
        expect(suggestVariableFor("company", ["business"])).toBe("business");
    });

    it("leaves a field blank rather than guessing", () => {
        // The rule the whole helper exists for: a plausible-but-wrong value
        // proposed into a CRM field gets saved, and a blank gets noticed.
        expect(suggestVariableFor("phone", ["customer_name"])).toBe(null);
        expect(suggestVariableFor("email", ["origin_city"])).toBe(null);
        expect(suggestVariableFor("invoice_total", ["customer_name", "phone"])).toBe(null);
    });

    it("never matches on a partial word", () => {
        expect(suggestVariableFor("phone", ["telephone_consent"])).toBe(null);
        expect(suggestVariableFor("city", ["origin_city"])).toBe("origin_city");
    });

    it("does not let a very short parameter match everything", () => {
        expect(suggestVariableFor("id", ["order_id_reference"])).toBe(null);
    });

    it("proposes a whole tool's arguments and never overwrites one already set", () => {
        const parameters = [
            { name: "contact_name", required: true },
            { name: "phone", required: true },
            { name: "origin_city", required: true },
            { name: "invoice_total" },
        ];
        const filled = suggestArguments(parameters, extractionVariables(definition), {
            phone: "{{ initial_context.phone_number }}",
        });

        expect(filled.contact_name).toBe("{{ gathered_context.extracted_variables.customer_name }}");
        expect(filled.origin_city).toBe("{{ gathered_context.extracted_variables.origin_city }}");
        // Already set by the operator — a proposal must not clobber a decision.
        expect(filled.phone).toBe("{{ initial_context.phone_number }}");
        // Nothing collected resembles it, so it stays blank.
        expect(filled.invoice_total).toBeUndefined();
    });
});
