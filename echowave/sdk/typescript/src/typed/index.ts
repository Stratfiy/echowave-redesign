// GENERATED — do not edit by hand.
//
// Re-exports every typed node interface + factory. Also exports the
// `TypedNode` discriminated-union that `Workflow.addTyped` accepts.

export { type AgentNode, agentNode } from "./agent-node.js";
export { type Branch, branch } from "./branch.js";
export { type EndCall, endCall } from "./end-call.js";
export { type GlobalNode, globalNode } from "./global-node.js";
export { type Paygent, paygent } from "./paygent.js";
export { type Qa, qa } from "./qa.js";
export { type Sms, sms } from "./sms.js";
export { type StartCall, startCall } from "./start-call.js";
export { type Trigger, trigger } from "./trigger.js";
export { type Tuner, tuner } from "./tuner.js";
export { type Wait, wait } from "./wait.js";
export { type Webhook, webhook } from "./webhook.js";

import type {
    AgentNode,
    Branch,
    EndCall,
    GlobalNode,
    Paygent,
    Qa,
    Sms,
    StartCall,
    Trigger,
    Tuner,
    Wait,
    Webhook,
} from "./index.js";

/** Discriminated union of every generated typed node. */
export type TypedNode = AgentNode | Branch | EndCall | GlobalNode | Paygent | Qa | Sms | StartCall | Trigger | Tuner | Wait | Webhook;
