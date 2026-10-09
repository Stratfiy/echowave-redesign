import { describe, expect, it } from 'vitest';

import { ACCEPTED_FILE_TYPES, rejectFile } from '@/lib/uploadKnowledge';

// A business owner's lead list is as often an Excel export as a CSV. The
// paperclip refused it, so the only way in was "save it as CSV first"
// (found building an outreach agent end to end, October 2026). Kept in step
// with api/services/knowledge_base/extraction.py, which now reads .xlsx.
describe('the paperclip takes a lead list in Excel', () => {
    it('accepts .xlsx', () => {
        expect(ACCEPTED_FILE_TYPES).toContain('.xlsx');
        const file = new File(['x'], 'Leads October.XLSX');
        expect(rejectFile(file)).toBeNull();
    });

    it('accepts pictures, which are read for what they show and say', () => {
        for (const name of ['board.png', 'Receipt.JPG', 'scan.jpeg', 'screenshot.webp']) {
            expect(rejectFile(new File(['x'], name))).toBeNull();
        }
    });

    it('still refuses what cannot be read', () => {
        expect(rejectFile(new File(['x'], 'old.doc'))).not.toBeNull();
        expect(rejectFile(new File(['x'], 'macro.xlsm'))).not.toBeNull();
    });
});
