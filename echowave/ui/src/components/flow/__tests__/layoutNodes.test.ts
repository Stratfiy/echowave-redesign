import { describe, expect, it, vi } from 'vitest';

import { layoutNodes } from '@/app/workflow/[workflowId]/utils/layoutNodes';
import type { FlowEdge, FlowNode } from '@/components/flow/types';

describe('canvas layout', () => {
    it('keeps branches and waits in the left-to-right execution path without mutating definitions', () => {
        vi.useFakeTimers();
        const nodes: FlowNode[] = ['startCall', 'branch', 'wait', 'endCall'].map((type, index) => ({
            id: String(index), type, position: { x: 0, y: 0 }, data: { name: type, prompt: 'keep me' },
        }));
        const edges: FlowEdge[] = [0, 1, 2].map(index => ({
            id: String(index), source: String(index), target: String(index + 1), data: { condition: '', label: '' },
        }));
        const result = layoutNodes(nodes, edges, 'LR', { current: null });
        expect(result.map(node => node.id)).toEqual(nodes.map(node => node.id));
        for (let index = 1; index < result.length; index++) {
            expect(result[index].position.x).toBeGreaterThan(result[index - 1].position.x + 280);
            expect(result[index].position.y).toBe(result[0].position.y);
        }
        expect(nodes.every(node => node.position.x === 0)).toBe(true);
        expect(result.every((node, index) => node.data === nodes[index].data)).toBe(true);
        vi.runAllTimers();
        vi.useRealTimers();
    });

    it('uses measured dimensions to avoid overlap', () => {
        vi.useFakeTimers();
        const nodes: FlowNode[] = [0, 1].map(index => ({
            id: String(index), type: 'agentNode', position: { x: 0, y: 0 },
            measured: { width: 500, height: 300 }, data: { name: 'Agent' },
        }));
        const edges: FlowEdge[] = [{ id: 'edge', source: '0', target: '1', data: { condition: '', label: '' } }];
        const result = layoutNodes(nodes, edges, 'LR', { current: null });
        expect(result[1].position.x).toBeGreaterThan(result[0].position.x + 500);
        vi.runAllTimers();
        vi.useRealTimers();
    });
});
