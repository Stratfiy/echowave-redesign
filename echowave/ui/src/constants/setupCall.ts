/**
 * Where somebody goes when they would rather we set the agent up with them.
 *
 * This replaced "Hire an Expert", and the rename is the point rather than
 * decoration. The whole pitch of this product is that a business does not need
 * to pay an agency to run a voice agent — and the most prominent, permanently
 * visible control in the sidebar was an offer to sell them one. It argued
 * against the thing we are selling, on every screen.
 *
 * "Get it set up — free" says the same helpful thing without contradicting the
 * positioning: the setup is included, not billed. That is also the shape the
 * commission-based field model needs, where a rep's job is to get the first
 * agent live rather than to be retained.
 *
 * A booking link rather than a lead form, because the two ask for the same
 * information and only one of them ends with a time in the diary. The lead
 * modal is still mounted and still used by the enterprise path; this entry
 * point simply no longer routes through it.
 *
 * Plain constant, same reasoning as `community.ts`: it is a public URL, it is
 * published elsewhere too, and one file is easier to correct than three
 * deployment environments.
 */

export const SETUP_CALL_URL = "https://calendly.com/nautomationlabs/30min";

/** The label, in one place — it appears in the sidebar and in the nudge. */
export const SETUP_CALL_LABEL = "Get it set up — free";

/** The sub-line under the label wherever there is room for one. */
export const SETUP_CALL_BLURB = "Book 30 minutes and we'll build it with you";
