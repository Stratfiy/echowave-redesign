/**
 * Every number the workspace has, as one list (Settings -> Phone numbers).
 *
 * The API files numbers under the carrier configuration they belong to, so a
 * workspace with a bought number and its own carrier had to open two pages
 * to see both. This flattens them, says what kind each is, and who uses it:
 * the agent that answers it, the agent that calls back on it, and whether it
 * is the number calls go out from. Pure, so the list can be tested without
 * a screen.
 */

import type { PhoneNumberResponse, TelephonyConfigurationListItem, VerifiedNumber } from "@/client/types.gen";

export type NumberUse =
  | { kind: "answers"; workflowId: number; name: string }
  | { kind: "calls_back"; workflowId: number; name: string }
  | { kind: "calls_out" }
  | { kind: "test_only" }
  | { kind: "unused" };

export type NumberRow = {
  key: string;
  address: string;
  /** "Bought", the carrier's name, or "Your caller ID". */
  type: string;
  status: "live" | "paused" | "checking" | "released";
  uses: NumberUse[];
  /** Where to change it: present for numbers on a carrier configuration. */
  configId?: number;
  phoneNumberId?: number;
  inboundWorkflowId?: number | null;
};

function statusOf(number: PhoneNumberResponse): NumberRow["status"] {
  if (number.released_at || number.status === "released") return "released";
  return number.is_active === false ? "paused" : "live";
}

export function numberRows(
  configs: TelephonyConfigurationListItem[],
  numbersByConfig: Record<number, PhoneNumberResponse[]>,
  verified: VerifiedNumber[],
): NumberRow[] {
  const rows: NumberRow[] = [];
  for (const config of configs) {
    for (const number of numbersByConfig[config.id] ?? []) {
      const status = statusOf(number);
      if (status === "released") continue;
      const uses: NumberUse[] = [];
      if (number.inbound_workflow_id) {
        uses.push({
          kind: "answers",
          workflowId: number.inbound_workflow_id,
          name: number.inbound_workflow_name ?? `Agent ${number.inbound_workflow_id}`,
        });
      }
      if (number.callback_workflow_id && number.callback_workflow_id !== number.inbound_workflow_id) {
        uses.push({
          kind: "calls_back",
          workflowId: number.callback_workflow_id,
          name: `Agent ${number.callback_workflow_id}`,
        });
      }
      if (number.is_default_caller_id && config.is_default_outbound) uses.push({ kind: "calls_out" });
      if (uses.length === 0) uses.push({ kind: "unused" });
      rows.push({
        key: `n-${number.id}`,
        address: number.address,
        type: config.is_platform_managed ? "Bought" : config.name,
        status,
        uses,
        configId: config.id,
        phoneNumberId: number.id,
        inboundWorkflowId: number.inbound_workflow_id ?? null,
      });
    }
  }
  for (const number of verified) {
    rows.push({
      key: `v-${number.phone_number}`,
      address: number.phone_number,
      type: "Your caller ID",
      status: number.status === "verified" ? "live" : "checking",
      uses: [{ kind: "test_only" }],
    });
  }
  return rows;
}
