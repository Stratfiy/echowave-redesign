import { describe, expect, it } from "vitest";

import type { PhoneNumberResponse, TelephonyConfigurationListItem } from "@/client/types.gen";

import { numberRows } from "../numberRows";

function config(over: Partial<TelephonyConfigurationListItem>): TelephonyConfigurationListItem {
  return {
    id: 1,
    name: "Plivo",
    provider: "plivo",
    is_default_outbound: false,
    is_platform_managed: false,
    phone_number_count: 1,
    created_at: "",
    updated_at: "",
    ...over,
  } as TelephonyConfigurationListItem;
}

function number(over: Partial<PhoneNumberResponse>): PhoneNumberResponse {
  return {
    id: 10,
    telephony_configuration_id: 1,
    address: "+918047182290",
    is_active: true,
    is_default_caller_id: false,
    inbound_workflow_id: null,
    callback_workflow_id: null,
    ...over,
  } as PhoneNumberResponse;
}

describe("every number on one list", () => {
  it("says a bought number is bought and who answers it", () => {
    const rows = numberRows(
      [config({ id: 1, is_platform_managed: true })],
      { 1: [number({ inbound_workflow_id: 7, inbound_workflow_name: "Riya" })] },
      [],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].type).toBe("Bought");
    expect(rows[0].uses).toEqual([{ kind: "answers", workflowId: 7, name: "Riya" }]);
  });

  it("names the carrier for a number on your own carrier", () => {
    const rows = numberRows([config({ id: 2, name: "Exotel" })], { 2: [number({ id: 11 })] }, []);
    expect(rows[0].type).toBe("Exotel");
  });

  it("marks a number nobody uses as unused", () => {
    const rows = numberRows([config({})], { 1: [number({})] }, []);
    expect(rows[0].uses).toEqual([{ kind: "unused" }]);
  });

  it("says when calls go out from a number", () => {
    const rows = numberRows([config({ is_default_outbound: true })], { 1: [number({ is_default_caller_id: true })] }, []);
    expect(rows[0].uses).toEqual([{ kind: "calls_out" }]);
  });

  it("leaves released numbers off", () => {
    const rows = numberRows([config({})], { 1: [number({ released_at: "2026-10-01T00:00:00Z" })] }, []);
    expect(rows).toEqual([]);
  });

  it("lists verified caller IDs as test-only", () => {
    const rows = numberRows([], {}, [{ phone_number: "+919845011223", status: "pending" }]);
    expect(rows[0]).toMatchObject({ type: "Your caller ID", status: "checking", uses: [{ kind: "test_only" }] });
    expect(rows[0].configId).toBeUndefined();
  });
});
