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

export interface CredentialField {
    key: string;
    label: string;
    placeholder: string;
    /** Rendered as a password input, and never echoed back by the server. */
    isSecret?: boolean;
}

export function credentialFields(type: WebhookCredentialType): CredentialField[] {
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
        default:
            return [];
    }
}

/** The types the create and rotate forms offer, in the order they offer them. */
export const CREDENTIAL_TYPES: { value: WebhookCredentialType; label: string }[] = [
    { value: "bearer_token", label: "Bearer Token" },
    { value: "api_key", label: "API Key" },
    { value: "basic_auth", label: "Basic Auth" },
    { value: "custom_header", label: "Custom Header" },
];
