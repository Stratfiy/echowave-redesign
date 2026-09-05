/**
 * Where a customer goes when the product has not answered their question.
 *
 * Two channels rather than one, because the audiences do not overlap. The buyer
 * this product is aimed at — a clinic owner, a dealership — is on WhatsApp and
 * will not create a Slack account to ask why a call dropped. Somebody wiring up
 * the API is already in Slack all day and will not want a WhatsApp group. Each
 * is the wrong answer for the other, so both are offered and neither is
 * presented as the main one.
 *
 * Plain constants rather than environment variables: these are published on the
 * marketing site too, so there is nothing to keep out of the repository, and a
 * value that lives in one file is easier to change than one that has to be
 * right in three deployment environments. Invite links can be revoked — if one
 * stops working, it is a one-line change here.
 */

export const COMMUNITY_URLS = {
    /** Public docs. Already the target of the header's help button. */
    documentation: "https://docs.decibyl.ai",
    whatsapp: "https://chat.whatsapp.com/Ebd9nygrUZg37RVqgjnOYA",
    slack:
        "https://join.slack.com/t/decibyl/shared_invite/zt-48zc1yr9x-au6xUu7i6nl23l7XSjtgKg",
} as const;

/** One entry per row of the help menu, in the order they are offered. */
export const HELP_LINKS = [
    {
        key: "documentation",
        label: "Documentation",
        // What the reader gets, not what the thing is called. "Docs" says where
        // it goes; this says whether it is worth going.
        hint: "Guides and reference",
        href: COMMUNITY_URLS.documentation,
    },
    {
        key: "whatsapp",
        label: "Ask on WhatsApp",
        hint: "Our community, no sign-up",
        href: COMMUNITY_URLS.whatsapp,
    },
    {
        key: "slack",
        label: "Join our Slack",
        hint: "For teams already on Slack",
        href: COMMUNITY_URLS.slack,
    },
] as const;
