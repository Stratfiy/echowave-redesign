import { beforeEach, expect, it } from 'vitest';

import { FlowEdge, FlowNode } from '@/components/flow/types';

import { graphSnapshot, useWorkflowStore } from '../workflowStore';

const nodes: FlowNode[] = [{ id: 'a', type: 'agentNode', position: { x: 0, y: 0 }, data: { name: 'A', prompt: 'Original' } }];
const edge: FlowEdge = { id: 'e', source: 'a', target: 'a', data: { label: 'Original', condition: 'Continue' } };
const state = () => useWorkflowStore.getState();
beforeEach(() => state().initializeWorkflow(39, 'Agent', nodes, [edge]));

it('records consecutive node edits with no skipped or duplicate undo steps', () => {
  state().updateNode('a', { data: { ...nodes[0].data, prompt: 'First' } });
  state().updateNode('a', { data: { ...nodes[0].data, prompt: 'Second' } });
  state().undo(); expect(state().nodes[0].data.prompt).toBe('First');
  state().undo(); expect(state().nodes[0].data.prompt).toBe('Original');
  state().redo(); expect(state().nodes[0].data.prompt).toBe('First');
  state().redo(); expect(state().nodes[0].data.prompt).toBe('Second');
});

it('restores complete edge Apply and Delete states in both directions', () => {
  state().updateEdge('e', { data: { ...edge.data, label: 'Edited' } });
  state().deleteEdge('e');
  state().undo(); expect(state().edges[0].data.label).toBe('Edited');
  state().undo(); expect(state().edges[0].data.label).toBe('Original');
  state().redo(); expect(state().edges[0].data.label).toBe('Edited');
  state().redo(); expect(state().edges).toEqual([]);
});

it('groups drag frames into one edit from the original to final position', () => {
  for (const x of [10, 20]) state().setNodes([{ ...nodes[0], position: { x, y: 0 } }], [{ id: 'a', type: 'position', position: { x, y: 0 }, dragging: true }]);
  state().setNodes([{ ...nodes[0], position: { x: 30, y: 0 } }], [{ id: 'a', type: 'position', position: { x: 30, y: 0 }, dragging: false }]);
  expect(state().history).toHaveLength(2);
  state().undo(); expect(state().nodes[0].position.x).toBe(0);
  state().redo(); expect(state().nodes[0].position.x).toBe(30);
});

it('deduplicates consecutive proposals and arrangement checkpoints', () => {
  for (const x of [100, 200]) {
    state().applyGraphProposal(39, graphSnapshot(state().nodes, state().edges), [{ ...nodes[0], position: { x, y: 0 } }], [edge]);
  }
  expect(state().history).toHaveLength(3);
  state().undo(); expect(state().nodes[0].position.x).toBe(100);
  state().undo(); expect(state().nodes[0].position.x).toBe(0);
  state().redo(); state().redo(); expect(state().nodes[0].position.x).toBe(200);
});

it('ignores runtime and connection highlight paint when checkpointing the next edit', () => {
  state().setNodes([{ ...nodes[0], data: { ...nodes[0].data, runtime_active: true, selected_through_edge: true, hovered_through_edge: true } }]);
  state().updateEdge('e', { data: { ...edge.data, label: 'Edited' } });
  expect(state().history).toHaveLength(2);
  state().undo(); expect(state().edges[0].data.label).toBe('Original');
});

it('drops redo only for a real new edit, not selection or identical Apply', () => {
  state().updateEdge('e', { data: { ...edge.data, label: 'First' } });
  state().updateEdge('e', { data: { ...edge.data, label: 'Second' } });
  state().undo();
  state().setNodes([{ ...nodes[0], selected: true }], [{ id: 'a', type: 'select', selected: true }]);
  state().updateEdge('e', { data: { ...edge.data, label: 'First' } });
  expect(state().canRedo()).toBe(true);
  state().updateEdge('e', { data: { ...edge.data, label: 'Branch' } });
  expect(state().canRedo()).toBe(false);
  state().undo(); expect(state().edges[0].data.label).toBe('First');
  state().redo(); expect(state().edges[0].data.label).toBe('Branch');
});

it('undoes node deletion with its attached edges and redoes additions', () => {
  state().deleteNode('a');
  state().undo(); expect(state().nodes).toEqual(nodes); expect(state().edges).toEqual([edge]);
  state().redo(); expect(state().nodes).toEqual([]); expect(state().edges).toEqual([]);
  state().addNode(nodes[0]); state().addEdge(edge);
  state().undo(); expect(state().edges).toEqual([]);
  state().undo(); expect(state().nodes).toEqual([]);
  state().redo(); state().redo(); expect(state().edges).toEqual([edge]);
});

it('treats accepted server normalization as the current checkpoint, not an extra edit', () => {
  state().updateNode('a', { data: { ...nodes[0].data, prompt: 'Saved' } });
  const normalized = [{ ...state().nodes[0], data: { ...state().nodes[0].data, trigger_path: 'server-minted-path' } }];
  expect(state().acceptSavedGraph(39, state().editorSession, graphSnapshot(state().nodes, state().edges), 'Agent', normalized, state().edges)).toBe(true);
  state().updateNode('a', { data: { ...normalized[0].data, prompt: 'Next' } });
  expect(state().history).toHaveLength(3);
  state().undo();
  expect(state().nodes[0].data.prompt).toBe('Saved');
  expect(state().nodes[0].data.trigger_path).toBe('server-minted-path');
  state().undo(); expect(state().nodes[0].data.prompt).toBe('Original');
  state().redo();
  expect(state().nodes[0].data.prompt).toBe('Saved');
  expect(state().nodes[0].data.trigger_path).toBe('server-minted-path');
  state().redo(); expect(state().nodes[0].data.prompt).toBe('Next');
});

it('combines React Flow node and attached-edge removal callbacks into one checkpoint', () => {
  state().setNodes([], [{ id: 'a', type: 'remove' }]);
  expect(state().edges).toEqual([]);
  state().setEdges([], [{ id: 'e', type: 'remove' }]);
  expect(state().history).toHaveLength(2);
  state().undo();
  expect(state().nodes).toEqual(nodes);
  expect(state().edges).toEqual([edge]);
  state().redo();
  expect(state().nodes).toEqual([]);
  expect(state().edges).toEqual([]);
});

it('deletes selected nodes and edges atomically before React Flow splits callbacks', () => {
  const other: FlowNode = { ...nodes[0], id: 'b' };
  const attached: FlowEdge = { ...edge, id: 'attached', target: 'b' };
  const selected: FlowEdge = { ...edge, id: 'selected', source: 'b', target: 'b' };
  const retained: FlowEdge = { ...selected, id: 'retained' };
  state().initializeWorkflow(39, 'Agent', [...nodes, other], [edge, attached, selected, retained]);
  state().deleteGraphElements(['a'], ['selected']);
  expect(state().nodes).toEqual([other]);
  expect(state().edges).toEqual([retained]);
  expect(state().history).toHaveLength(2);
  state().undo();
  expect(state().nodes).toEqual([...nodes, other]);
  expect(state().edges).toEqual([edge, attached, selected, retained]);
  state().redo();
  expect(state().nodes).toEqual([other]);
  expect(state().edges).toEqual([retained]);
});
