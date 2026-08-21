/**
 * What to send the store route, given what the provider-keys form knows.
 *
 * Pulled out of the page so the one decision that matters here — does this
 * key fan out to every component the vendor serves, or only to one — is
 * checkable without mounting the page. ``matchedComponents`` is undefined for
 * a vendor the registry has never heard of, which is why ``component`` (the
 * manually-picked fallback) is still an argument rather than something this
 * function derives on its own.
 *
 * Mirrors the backend's own rule in `platform_credentials.set_provider_key`:
 * ``apply_to_all_components`` only means something for a vendor with more
 * than one slot, and the leading component is whichever the registry lists
 * first for it.
 */
export function planKeySubmission({
    provider,
    component,
    matchedComponents,
    applyToAll,
}: {
    provider: string;
    component: string;
    matchedComponents: string[] | undefined;
    applyToAll: boolean;
}): { provider: string; component: string; apply_to_all_components: boolean } {
    const multiComponent = Boolean(matchedComponents) && matchedComponents!.length > 1;
    const fanOut = multiComponent && applyToAll;
    // Fanning out reports the vendor's first component — the route fills in
    // the rest from the registry, and the reported one is just which of them
    // leads. Anything else — a single-component vendor, an unrecognised one,
    // or a multi-component vendor explicitly narrowed down — reports exactly
    // the component that was picked, by hand or because there was only one.
    const resolvedComponent = fanOut
        ? matchedComponents![0]
        : (matchedComponents?.length === 1 ? matchedComponents[0] : component);
    return {
        provider: provider.trim().toLowerCase(),
        component: resolvedComponent,
        apply_to_all_components: fanOut,
    };
}
