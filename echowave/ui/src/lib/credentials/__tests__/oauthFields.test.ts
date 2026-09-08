/**
 * The required/optional split on an OAuth credential.
 *
 * The rotate dialog gated its save on *every* field being filled — correct
 * while every credential type's fields were all mandatory, and wrong the
 * moment one had defaults. An OAuth grant leaves `header_prefix`,
 * `header_name` and `scope` blank to mean "use the common case", so requiring
 * them would have left the form unsubmittable for every vendor except Zoho.
 *
 * The four grant fields mirror `REQUIRED_FIELDS` in
 * `api/services/integrations/oauth2.py`. If they drift, the form accepts a
 * credential the server then refuses with a 400 listing the same names back.
 */

import { describe, expect, it } from "vitest";

import {
    CREDENTIAL_TYPES,
    credentialFields,
    missingRequired,
    requiredFields,
} from "../fields";

const GRANT = {
    token_url: "https://accounts.zoho.in/oauth/v2/token",
    client_id: "cid",
    client_secret: "secret",
    refresh_token: "refresh",
};

describe("the OAuth credential form", () => {
    it("is offered as a type", () => {
        expect(CREDENTIAL_TYPES.map((t) => t.value)).toContain("oauth2");
    });

    it("requires exactly the four grant fields", () => {
        expect(requiredFields("oauth2").sort()).toEqual(
            ["client_id", "client_secret", "refresh_token", "token_url"].sort(),
        );
    });

    it("submits with the grant alone", () => {
        expect(missingRequired("oauth2", GRANT)).toEqual([]);
    });

    it("names what is missing rather than just refusing", () => {
        expect(missingRequired("oauth2", { token_url: GRANT.token_url })).toEqual([
            "client_id",
            "client_secret",
            "refresh_token",
        ]);
    });

    it("treats whitespace as missing", () => {
        expect(missingRequired("oauth2", { ...GRANT, client_secret: "  " })).toEqual([
            "client_secret",
        ]);
    });

    it("keeps the secrets masked", () => {
        const secret = credentialFields("oauth2")
            .filter((f) => f.isSecret)
            .map((f) => f.key);
        expect(secret).toContain("client_secret");
        expect(secret).toContain("refresh_token");
    });

    it("explains the Zoho scheme on the field that causes it", () => {
        // Sending Bearer to Zoho 401s with nothing that mentions the scheme.
        const prefix = credentialFields("oauth2").find(
            (f) => f.key === "header_prefix",
        );
        expect(prefix?.optional).toBe(true);
        expect(prefix?.hint).toMatch(/Zoho-oauthtoken/);
    });
});

describe("the older credential types", () => {
    it("still require every field they have", () => {
        // None of them marks anything optional, so the previous behaviour --
        // save disabled until the form is full -- is unchanged.
        for (const type of ["bearer_token", "api_key", "basic_auth", "custom_header"] as const) {
            expect(requiredFields(type)).toEqual(
                credentialFields(type).map((f) => f.key),
            );
        }
    });
});
