/**
 * Feature switches from the staff console (ADMIN-1): the shapes the
 * `/admin/features` routes return, and the calls the two screens make.
 *
 * The generated client types these responses as `{[key: string]: unknown}`
 * (the routes return plain dicts), so the shapes are declared once here
 * rather than cast in each screen.
 */

import {
    clearGlobalOverrideApiV1AdminFeaturesNameGlobalDelete,
    clearOrganizationOverrideApiV1AdminFeaturesNameOrganizationsOrganizationIdDelete,
    listAccountsApiV1AdminBillingAccountsGet,
    listFeaturesApiV1AdminFeaturesGet,
    organizationFeaturesApiV1AdminFeaturesOrganizationsOrganizationIdGet,
    setGlobalOverrideApiV1AdminFeaturesNameGlobalPut,
    setOrganizationOverrideApiV1AdminFeaturesNameOrganizationsOrganizationIdPut,
} from "@/client/sdk.gen";
import { detailFromResult } from "@/lib/apiError";

export type GlobalSource = "console" | "environment";

export interface FlagOverride {
    organization_id: number | null;
    organization_name: string | null;
    enabled: boolean;
    note: string | null;
    expires_at: string | null;
    expired: boolean;
    set_by_user_id: number | null;
    updated_at: string | null;
}

export interface FlagRow {
    name: string;
    description: string;
    setting: string;
    global_enabled: boolean;
    global_source: GlobalSource;
    environment_enabled: boolean;
    global_override: FlagOverride | null;
    overrides: FlagOverride[];
    environment_organization_ids: number[];
}

export interface OrgFlagRow {
    name: string;
    description: string;
    enabled: boolean;
    global_enabled: boolean;
    global_source: GlobalSource;
    override: FlagOverride | null;
    environment_listed: boolean;
}

export interface AccountMatch {
    organization_id: number;
    name: string;
    owner_email: string | null;
}

/** Either the value, or a sentence saying why not. */
export type Outcome<T> = { ok: true; value: T } | { ok: false; error: string };

function fail<T>(result: { error?: unknown; response?: { status?: number } | null }, fallback: string): Outcome<T> {
    return { ok: false, error: detailFromResult(result, fallback) };
}

export async function loadFlags(): Promise<Outcome<FlagRow[]>> {
    const result = await listFeaturesApiV1AdminFeaturesGet();
    if (result.error || !result.data) return fail(result, "Could not load flags");
    return { ok: true, value: (result.data as { flags?: FlagRow[] }).flags ?? [] };
}

export async function loadOrgFlags(organizationId: number): Promise<Outcome<OrgFlagRow[]>> {
    const result = await organizationFeaturesApiV1AdminFeaturesOrganizationsOrganizationIdGet({
        path: { organization_id: organizationId },
    });
    if (result.error || !result.data) return fail(result, "Could not load flags");
    return { ok: true, value: (result.data as { flags?: OrgFlagRow[] }).flags ?? [] };
}

export async function setForOrganization(
    name: string,
    organizationId: number,
    body: { enabled: boolean; note?: string | null; expires_at?: string | null },
): Promise<Outcome<null>> {
    const result = await setOrganizationOverrideApiV1AdminFeaturesNameOrganizationsOrganizationIdPut({
        path: { name, organization_id: organizationId },
        body: { enabled: body.enabled, note: body.note ?? null, expires_at: body.expires_at ?? null },
    });
    if (result.error) return fail(result, "Could not save");
    return { ok: true, value: null };
}

export async function clearForOrganization(name: string, organizationId: number): Promise<Outcome<null>> {
    const result = await clearOrganizationOverrideApiV1AdminFeaturesNameOrganizationsOrganizationIdDelete({
        path: { name, organization_id: organizationId },
    });
    if (result.error) return fail(result, "Could not remove");
    return { ok: true, value: null };
}

export async function setGlobal(name: string, enabled: boolean, confirm: string): Promise<Outcome<null>> {
    const result = await setGlobalOverrideApiV1AdminFeaturesNameGlobalPut({
        path: { name },
        body: { enabled, confirm },
    });
    if (result.error) return fail(result, "Could not change it for everyone");
    return { ok: true, value: null };
}

export async function clearGlobal(name: string, confirm: string): Promise<Outcome<null>> {
    const result = await clearGlobalOverrideApiV1AdminFeaturesNameGlobalDelete({
        path: { name },
        query: { confirm },
    });
    if (result.error) return fail(result, "Could not hand it back to the environment");
    return { ok: true, value: null };
}

/** Accounts matching a name or an email, from the billing accounts list. */
export async function searchAccounts(q: string): Promise<Outcome<AccountMatch[]>> {
    const result = await listAccountsApiV1AdminBillingAccountsGet({
        query: q.trim() ? { q: q.trim() } : {},
    });
    if (result.error || !result.data) return fail(result, "Could not search accounts");
    const accounts = ((result.data as { accounts?: AccountMatch[] }).accounts ?? []).map((a) => ({
        organization_id: a.organization_id,
        name: a.name,
        owner_email: a.owner_email ?? null,
    }));
    return { ok: true, value: accounts };
}

/** What a chip calls an organisation: its name, or its number. */
export function orgLabel(o: { organization_id: number | null; organization_name: string | null }): string {
    return o.organization_name || `Organization ${o.organization_id}`;
}

/** Where the global value comes from, in words. */
export function sourceLabel(source: GlobalSource): string {
    return source === "console" ? "set here" : "from environment";
}
