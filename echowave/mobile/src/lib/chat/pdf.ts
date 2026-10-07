/**
 * A photo as a one-page PDF.
 *
 * Chat attachments go through the knowledge base, which reads PDF, Word,
 * text, Markdown, CSV, JSON and HTML -- not images
 * (api/services/knowledge_base/extraction.py). A PDF with no text layer is
 * OCR'd there when tesseract is installed (knowledge_base/ocr.py). So a
 * photo of a bill or a letter is wrapped, on the phone, as the single image
 * of a one-page PDF: the JPEG bytes are embedded untouched (DCTDecode), no
 * re-encoding, no library.
 */

export type JpegInfo = { width: number; height: number; components: number };

/** Width, height and colour components from a JPEG's SOF marker. */
export function readJpeg(bytes: Uint8Array): JpegInfo | null {
    if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) return null;
    let i = 2;
    while (i + 9 < bytes.length) {
        if (bytes[i] !== 0xff) {
            i += 1;
            continue;
        }
        const marker = bytes[i + 1];
        // Standalone markers carry no length.
        if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
            i += 2;
            continue;
        }
        const length = (bytes[i + 2] << 8) | bytes[i + 3];
        const isSof = marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc;
        if (isSof) {
            const height = (bytes[i + 5] << 8) | bytes[i + 6];
            const width = (bytes[i + 7] << 8) | bytes[i + 8];
            const components = bytes[i + 9];
            if (!width || !height) return null;
            return { width, height, components };
        }
        i += 2 + length;
    }
    return null;
}

const encoder = new TextEncoder();

function concat(parts: (Uint8Array | string)[]): Uint8Array {
    const chunks = parts.map((p) => (typeof p === 'string' ? encoder.encode(p) : p));
    const total = chunks.reduce((n, c) => n + c.length, 0);
    const out = new Uint8Array(total);
    let at = 0;
    for (const c of chunks) {
        out.set(c, at);
        at += c.length;
    }
    return out;
}

/** A4 width in points; the page height follows the photo's shape. */
const PAGE_WIDTH = 595;

export function jpegToPdf(jpeg: Uint8Array): Uint8Array {
    const info = readJpeg(jpeg);
    if (!info) throw new Error('That photo could not be read.');
    const colour = info.components === 1 ? '/DeviceGray' : info.components === 4 ? '/DeviceCMYK' : '/DeviceRGB';
    const pageW = PAGE_WIDTH;
    const pageH = Math.max(1, Math.round((info.height / info.width) * PAGE_WIDTH));
    const content = `q ${pageW} 0 0 ${pageH} 0 0 cm /Im0 Do Q`;

    const objects: (Uint8Array | string)[][] = [
        ['<< /Type /Catalog /Pages 2 0 R >>'],
        ['<< /Type /Pages /Kids [3 0 R] /Count 1 >>'],
        [`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageW} ${pageH}] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>`],
        [
            `<< /Type /XObject /Subtype /Image /Width ${info.width} /Height ${info.height} /ColorSpace ${colour} /BitsPerComponent 8 /Filter /DCTDecode${info.components === 4 ? ' /Decode [1 0 1 0 1 0 1 0]' : ''} /Length ${jpeg.length} >>\nstream\n`,
            jpeg,
            '\nendstream',
        ],
        [`<< /Length ${content.length} >>\nstream\n${content}\nendstream`],
    ];

    const parts: (Uint8Array | string)[] = ['%PDF-1.4\n%\xE2\xE3\xCF\xD3\n'];
    const offsets: number[] = [];
    let length = concat(parts).length;
    objects.forEach((body, index) => {
        offsets.push(length);
        const object = concat([`${index + 1} 0 obj\n`, ...body, '\nendobj\n']);
        parts.push(object);
        length += object.length;
    });
    const xrefAt = length;
    const xref = [
        'xref\n',
        `0 ${objects.length + 1}\n`,
        '0000000000 65535 f \n',
        ...offsets.map((o) => `${String(o).padStart(10, '0')} 00000 n \n`),
        `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\n`,
        `startxref\n${xrefAt}\n%%EOF\n`,
    ].join('');
    parts.push(xref);
    return concat(parts);
}

export function base64ToBytes(base64: string): Uint8Array {
    const binary = atob(base64);
    const out = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
    return out;
}
