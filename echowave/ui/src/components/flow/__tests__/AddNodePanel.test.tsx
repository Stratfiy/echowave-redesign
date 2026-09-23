import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { NodeSpec } from '@/client/types.gen';

import AddNodePanel from '../AddNodePanel';
import { NodeType } from '../types';

const { specs } = vi.hoisted(() => ({
    specs: [
        { name: 'trigger', display_name: 'Incoming message', description: 'Start when a message arrives', category: 'trigger', icon: 'MessageSquare', properties: [], graph_constraints: { max_instances: 1 } },
        { name: 'agentNode', display_name: 'Agent', description: 'Answer using connected knowledge', category: 'call_node', icon: 'Agent', properties: [] },
        { name: 'webhook', display_name: 'Webhook', description: 'Send a request to an external app', category: 'integration', icon: 'Webhook', properties: [] },
    ] satisfies NodeSpec[],
}));

vi.mock('@/components/flow/renderer', () => ({ useNodeSpecs: () => ({ specs }) }));

describe('AddNodePanel', () => {
    it('removes every picker control when closed and focuses search when opened', () => {
        const props = { onClose: vi.fn(), onNodeSelect: vi.fn(), nodes: [] };
        const { container, rerender } = render(<AddNodePanel {...props} isOpen={false} />);
        expect(container.childElementCount).toBe(0);
        rerender(<AddNodePanel {...props} isOpen />);
        expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Search nodes' }));
        rerender(<AddNodePanel {...props} isOpen={false} />);
        expect(screen.queryByRole('textbox')).toBeNull();
        expect(screen.queryAllByRole('button')).toHaveLength(0);
    });

    it('filters by description without case sensitivity and explains an empty result', () => {
        render(<AddNodePanel isOpen onClose={vi.fn()} onNodeSelect={vi.fn()} nodes={[]} />);
        const search = screen.getByRole('textbox', { name: 'Search nodes' });
        fireEvent.change(search, { target: { value: '  KNOWLEDGE  ' } });
        expect(screen.getByRole('button', { name: 'Agent' })).toBeTruthy();
        expect(screen.queryByRole('button', { name: /Incoming message/ })).toBeNull();
        expect(screen.queryByRole('button', { name: /Webhook/ })).toBeNull();
        expect(screen.getByText('1 node available')).toBeTruthy();
        fireEvent.change(search, { target: { value: 'nonexistent' } });
        expect(screen.getByText(/No matching nodes/)).toBeTruthy();
        expect(screen.getByText('0 nodes available')).toBeTruthy();
    });

    it('prevents adding a capped node while allowing other registered nodes', () => {
        const onNodeSelect = vi.fn();
        render(<AddNodePanel isOpen onClose={vi.fn()} onNodeSelect={onNodeSelect} nodes={[
            { id: 'existing-trigger', type: NodeType.TRIGGER, position: { x: 0, y: 0 }, data: { name: 'Incoming message' } },
        ]} />);
        const capped = screen.getByRole('button', { name: 'Incoming message' }) as HTMLButtonElement;
        expect(capped.disabled).toBe(true);
        expect(capped.getAttribute('aria-description')).toBe('Already at the limit for this agent');
        fireEvent.click(capped);
        expect(onNodeSelect).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Agent' }));
        expect(onNodeSelect).toHaveBeenCalledExactlyOnceWith(NodeType.AGENT_NODE);
    });

    it('closes with Escape from the search field without bubbling to the canvas', () => {
        const onClose = vi.fn();
        const onCanvasKeyDown = vi.fn();
        render(<div onKeyDown={onCanvasKeyDown}><AddNodePanel isOpen onClose={onClose} onNodeSelect={vi.fn()} nodes={[]} /></div>);
        fireEvent.keyDown(screen.getByRole('textbox', { name: 'Search nodes' }), { key: 'Escape' });
        expect(onClose).toHaveBeenCalledTimes(1);
        expect(onCanvasKeyDown).not.toHaveBeenCalled();
    });

    it('restores the opener focus and clears a previous search after reopening', () => {
        const opener = document.createElement('button');
        document.body.appendChild(opener);
        opener.focus();
        const props = { onClose: vi.fn(), onNodeSelect: vi.fn(), nodes: [] };
        const { rerender, unmount } = render(<AddNodePanel {...props} isOpen />);
        fireEvent.change(screen.getByRole('textbox'), { target: { value: 'webhook' } });
        rerender(<AddNodePanel {...props} isOpen={false} />);
        expect(document.activeElement).toBe(opener);
        rerender(<AddNodePanel {...props} isOpen />);
        expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('');
        expect(screen.getByText('3 nodes available')).toBeTruthy();
        unmount();
        opener.remove();
    });
});
