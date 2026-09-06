/**
 * Two disposition filters, on purpose.
 *
 * `dispositionCode` is how a call ended — `user_hangup`, `voicemail_detected`.
 * `callOutcome` is what it achieved. A call is legitimately both `user_hangup`
 * and `booked`: the customer got what they wanted and put the phone down. The
 * risk this guards is somebody tidying the two into one, which would force a
 * choice between two facts that are both true.
 */

import { describe, expect, it } from "vitest";

import { DISPOSITION_CODES } from "@/constants/dispositionCodes";
import {
    superadminFilterAttributes,
    usageFilterAttributes,
    workflowFilterAttributes,
} from "@/lib/filterAttributes";

const LISTS = {
    calls: usageFilterAttributes,
    workflow: workflowFilterAttributes,
    superadmin: superadminFilterAttributes,
};

describe("the call outcome filter", () => {
    it.each(Object.entries(LISTS))("is offered on the %s list", (_name, list) => {
        expect(list.map((a) => a.id)).toContain("callOutcome");
    });

    it.each(Object.entries(LISTS))(
        "does not replace the end-reason filter on the %s list",
        (_name, list) => {
            expect(list.map((a) => a.id)).toContain("dispositionCode");
        },
    );

    it("offers any/all, because a call carries several outcomes", () => {
        const outcome = usageFilterAttributes.find((a) => a.id === "callOutcome");
        expect(outcome?.config.matchModes).toBe(true);
    });

    it("does not offer any/all for the end reason, which is one value", () => {
        const disposition = usageFilterAttributes.find((a) => a.id === "dispositionCode");
        expect(disposition?.config.matchModes).toBeFalsy();
    });

    it("shares no codes with the end reasons", () => {
        // Overlap would make the two menus look interchangeable when they are
        // answering different questions.
        const outcome = usageFilterAttributes.find((a) => a.id === "callOutcome");
        const overlap = (outcome?.config.options ?? []).filter((code) =>
            (DISPOSITION_CODES as readonly string[]).includes(code),
        );
        expect(overlap).toEqual([]);
    });
});
