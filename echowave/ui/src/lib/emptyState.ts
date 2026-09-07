/**
 * Which empty state a list is in.
 *
 * The distinction that matters is between having nothing and having filtered
 * everything away. Showing the same sentence for both is how a screen tells an
 * account with ten thousand calls that it has none, and sends a new account
 * hunting for a broken filter it never set.
 */

export type EmptyReason = "no-data" | "filtered-out";

export function emptyReason({
    hasFilters,
}: {
    /** Whether any filter or search is currently applied. */
    hasFilters: boolean;
}): EmptyReason {
    return hasFilters ? "filtered-out" : "no-data";
}
