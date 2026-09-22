import { beforeEach, expect, it } from 'vitest';

import { FlowEdge, FlowNode } from '@/components/flow/types';

import { useWorkflowStore } from '../workflowStore';

// As somebody editing an agent, I want the step the conversation starts from
// to survive a stray Backspace, so that a draft always has a way in. The node
// toolbar has always hidden Delete on the start step — "workflow always needs
// one" — but the canvas keyboard reached it anyway, through a path the toolbar
// does not guard.
const nodes: FlowNode[] = [
  { id: 'start', type: 'startCall', position: { x: 0, y: 0 }, data: { name: 'Start', greeting: 'Hello' } },
  { id: 'ask', type: 'agentNode', position: { x: 200, y: 0 }, data: { name: 'Ask', prompt: 'Ask the date' } },
];
const edges: FlowEdge[] = [{ id: 'start-ask', source: 'start', target: 'ask', data: { label: 'Begin', condition: 'Always' } }];
const state = () => useWorkflowStore.getState();
const ids = () => state().nodes.map((node) => node.id);
beforeEach(() => state().initializeWorkflow(39, 'Agent', nodes, edges));

it('keeps the start step when the canvas asks for it to be deleted', () => {
  state().deleteGraphElements(['start'], []);
  expect(ids()).toEqual(['start', 'ask']);
  expect(state().edges.map((edge) => edge.id)).toEqual(['start-ask']);
});

it('keeps the start step but removes the rest of a mixed selection', () => {
  state().deleteGraphElements(['start', 'ask'], []);
  expect(ids()).toEqual(['start']);
  // The connection went with the step that actually left.
  expect(state().edges).toEqual([]);
});

it('refuses a direct delete of the start step', () => {
  state().deleteNode('start');
  expect(ids()).toEqual(['start', 'ask']);
  expect(state().edges.map((edge) => edge.id)).toEqual(['start-ask']);
});

it('records no undo entry for a delete that removed nothing', () => {
  const before = state().history.length;
  state().deleteGraphElements(['start'], []);
  expect(state().history).toHaveLength(before);
  expect(state().isDirty).toBe(false);
});

it('still deletes ordinary steps and their connections', () => {
  state().deleteGraphElements(['ask'], []);
  expect(ids()).toEqual(['start']);
  expect(state().edges).toEqual([]);
  expect(state().isDirty).toBe(true);
});
