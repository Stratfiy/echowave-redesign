/**
 * The Mintlify component vocabulary, as Astro components.
 *
 * The remark plugin in `src/remark/inject-components.mjs` imports from this
 * barrel, so every name it knows about has to be exported here — the two lists
 * are the same list, and a name in one and not the other is a build failure
 * rather than a silent blank on a page. That is deliberate: a docs component
 * that renders nothing is worse than a build that stops.
 */

export { default as Accordion } from "./Accordion.astro";
export { default as AccordionGroup } from "./AccordionGroup.astro";
export { default as Card } from "./Card.astro";
export { default as CardGroup } from "./CardGroup.astro";
export { default as Check } from "./Check.astro";
export { default as CodeGroup } from "./CodeGroup.astro";
export { default as Columns } from "./Columns.astro";
export { default as Expandable } from "./Expandable.astro";
export { default as Frame } from "./Frame.astro";
export { default as Info } from "./Info.astro";
export { default as Note } from "./Note.astro";
export { default as ParamField } from "./ParamField.astro";
export { default as ResponseField } from "./ResponseField.astro";
export { default as Step } from "./Step.astro";
export { default as Steps } from "./Steps.astro";
export { default as Tab } from "./Tab.astro";
export { default as Tabs } from "./Tabs.astro";
export { default as Tip } from "./Tip.astro";
export { default as Warning } from "./Warning.astro";
