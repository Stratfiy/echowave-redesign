/** The shell's meeting-mode hook is filled only while `meeting_capture` is on. */

import { render, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { resetEntryPoints, useEntryPoint } from '@/lib/shell/chatEntryPoints';

import { meetingCaptureHref, MeetingModeRegistrar } from '../MeetingModeRegistrar';

const push = vi.hoisted(() => vi.fn());
const flag = vi.hoisted(() => ({ on: false }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
vi.mock('@/lib/features', () => ({ useFeature: () => flag.on }));

afterEach(() => {
    resetEntryPoints();
    push.mockReset();
});

describe('MeetingModeRegistrar', () => {
    it('registers nothing while the flag is off', () => {
        flag.on = false;
        render(<MeetingModeRegistrar />);
        const { result } = renderHook(() => useEntryPoint('meeting'));
        expect(result.current.available).toBe(false);
    });

    it('opens meeting capture from Chat, carrying the conversation', () => {
        flag.on = true;
        render(<MeetingModeRegistrar />);
        const { result } = renderHook(() => useEntryPoint('meeting'));
        expect(result.current.available).toBe(true);
        result.current.open({ threadId: 'abc 1', draft: '' });
        expect(push).toHaveBeenCalledWith('/meetings/new?thread=abc%201');
    });

    it('unregisters when unmounted', () => {
        flag.on = true;
        const view = render(<MeetingModeRegistrar />);
        view.unmount();
        const { result } = renderHook(() => useEntryPoint('meeting'));
        expect(result.current.available).toBe(false);
    });

    it('starts a meeting with no conversation too', () => {
        expect(meetingCaptureHref(null)).toBe('/meetings/new');
    });
});
