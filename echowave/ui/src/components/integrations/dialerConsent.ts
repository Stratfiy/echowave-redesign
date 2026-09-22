/**
 * What an admin agrees to before a dialer is connected (CR-2).
 *
 * DRAFT, for the founder to approve before DIALER_IMPORT_ENABLED goes on.
 * Every line states something the importer actually does -- the backend
 * copies answered, recorded calls of 20 seconds or more, keeps each 30 days,
 * stores the customer's number as its last four digits, and deletes it all
 * on disconnect -- so changing a fact here means changing the importer too.
 */
export const DIALER_CONSENT_LINES: readonly string[] = [
    "Every night, the calls your team answered on this dialer, and that it recorded, are copied to Decibyl and turned into text so the call coach can read them.",
    "Each call is kept for 30 days and then deleted. Disconnecting deletes everything copied from this dialer straight away.",
    "We keep the customer's number only as its last four digits. Your staff's names and numbers are kept, so each person's notes reach them.",
    "Only calls your dialer already records are copied. Telling customers and staff that calls are recorded stays with you, as it does today.",
];

export const DIALER_CONSENT_CHECKBOX =
    "I run this dialer account, and I agree to how its calls are handled above.";

export interface DialerField {
    key: string;
    label: string;
    placeholder?: string;
    optional?: boolean;
    secret?: boolean;
}

export interface DialerVendor {
    value: "exotel" | "smartflo";
    label: string;
    where: string;
    fields: DialerField[];
}

/** The fields each dialer's API needs, as its own settings page names them. */
export const DIALER_VENDORS: readonly DialerVendor[] = [
    {
        value: "exotel",
        label: "Exotel",
        where: "Exotel dashboard → API settings",
        fields: [
            { key: "api_key", label: "API key" },
            { key: "api_token", label: "API token", secret: true },
            { key: "account_sid", label: "Account SID" },
            { key: "subdomain", label: "API subdomain", placeholder: "api.exotel.com", optional: true },
        ],
    },
    {
        value: "smartflo",
        label: "Tata Smartflo",
        where: "Smartflo portal → API tokens (a token lasts up to 90 days)",
        fields: [{ key: "api_token", label: "API token", secret: true }],
    },
];

export function vendorLabel(value: string): string {
    return DIALER_VENDORS.find((v) => v.value === value)?.label ?? value;
}
