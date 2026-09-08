/**
 * What each credential type is made of.
 *
 * Shared rather than duplicated because two screens now write these secrets —
 * the create dialog and the rotate dialog on Settings — and a field list that
 * disagrees between them writes a credential the server accepts and the tool
 * cannot use. `validate_credential_data` in `api/routes/credentials.py` is the
 * authority; these keys mirror it exactly, and adding a type there without
 * adding it here shows the operator an empty form rather than a wrong one.
 */

import type { WebhookCredentialType } from "@/client/types.gen";

/**
 * The generated union plus `oauth2`, which the backend accepts but the client
 * has not been regenerated against yet (`npm run generate-client` needs a
 * running backend). Delete this alias and use `WebhookCredentialType` directly
 * once it carries the value.
 */
export type CredentialTypeValue = WebhookCredentialType | "oauth2";

export interface CredentialField {
    key: string;
    label: string;
    placeholder: string;
    /** Rendered as a password input, and never echoed back by the server. */
    isSecret?: boolean;
    /** One line under the input. Use it where a wrong value gives a 401 that
     *  looks like something else — the Zoho scheme being the reason this
     *  exists at all. */
    hint?: string;
    /** Left blank means "use the default", not "invalid". */
    optional?: boolean;
}

export function credentialFields(type: CredentialTypeValue): CredentialField[] {
    switch (type) {
        case "api_key":
            return [
                { key: "header_name", label: "Header Name", placeholder: "X-API-Key" },
                {
                    key: "api_key",
                    label: "API Key",
                    placeholder: "your-api-key",
                    isSecret: true,
                },
            ];
        case "bearer_token":
            return [
                {
                    key: "token",
                    label: "Token",
                    placeholder: "your-bearer-token",
                    isSecret: true,
                },
            ];
        case "basic_auth":
            return [
                { key: "username", label: "Username", placeholder: "username" },
                {
                    key: "password",
                    label: "Password",
                    placeholder: "password",
                    isSecret: true,
                },
            ];
        case "custom_header":
            return [
                {
                    key: "header_name",
                    label: "Header Name",
                    placeholder: "X-Custom-Header",
                },
                {
                    key: "header_value",
                    label: "Header Value",
                    placeholder: "header-value",
                    isSecret: true,
                },
            ];
        case "oauth2":
            // The refresh-token grant. Four fields are the grant itself; the
            // last three are the shape, and only differ for vendors that do
            // not follow the common case.
            return [
                {
                    key: "token_url",
                    label: "Token URL",
                    placeholder: "https://accounts.zoho.in/oauth/v2/token",
                    hint: "The vendor's token endpoint. Must be https — the refresh token and client secret are posted here every time.",
                },
                {
                    key: "client_id",
                    label: "Client ID",
                    placeholder: "1000.XXXXXXXX",
                },
                {
                    key: "client_secret",
                    label: "Client Secret",
                    placeholder: "client secret",
                    isSecret: true,
                },
                {
                    key: "refresh_token",
                    label: "Refresh Token",
                    placeholder: "refresh token",
                    isSecret: true,
                    hint: "The long-lived one. Decibyl exchanges it for an access token when a call needs one, and again when that expires.",
                },
                {
                    key: "header_prefix",
                    label: "Header Prefix",
                    placeholder: "Bearer",
                    optional: true,
                    hint: "Zoho needs Zoho-oauthtoken and rejects Bearer. Leave blank for everything else.",
                },
                {
                    key: "header_name",
                    label: "Header Name",
                    placeholder: "Authorization",
                    optional: true,
                },
                {
                    key: "scope",
                    label: "Scope",
                    placeholder: "ZohoCRM.modules.ALL",
                    optional: true,
                    hint: "Only if the vendor requires a scope on refresh as well as on the first grant.",
                },
            ];
        default:
            return [];
    }
}

/** The types the create and rotate forms offer, in the order they offer them. */
export const CREDENTIAL_TYPES: { value: CredentialTypeValue; label: string }[] = [
    { value: "bearer_token", label: "Bearer Token" },
    { value: "api_key", label: "API Key" },
    { value: "basic_auth", label: "Basic Auth" },
    { value: "custom_header", label: "Custom Header" },
    // Last because it is the one with a setup cost, not because it matters
    // least: it is the only type that keeps working past the first hour with
    // Zoho, HubSpot, Salesforce or Google.
    { value: "oauth2", label: "OAuth (refresh token)" },
];

/** Fields the server refuses a credential without. Mirrors `REQUIRED_FIELDS`
 *  in `api/services/integrations/oauth2.py`. */
export function requiredFields(type: CredentialTypeValue): string[] {
    return credentialFields(type)
        .filter((f) => !f.optional)
        .map((f) => f.key);
}

/** Which required fields the operator has not filled in yet. Checked before
 *  the round trip so a half-filled OAuth grant is caught on the form rather
 *  than by a 400 that lists the same names back. */
export function missingRequired(
    type: CredentialTypeValue,
    data: Record<string, string | undefined>,
): string[] {
    return requiredFields(type).filter((key) => !(data[key] || "").trim());
}
