'use client';

/**
 * The options a poster or ad creative request made, as a grid: each with
 * Download and Edit this one.
 *
 * The row carries image ids, never URLs -- a signed URL expires -- so each
 * tile asks for one when it draws (GET /images/{id}), through the session's
 * own workspace. Download fetches the file the same way. Edit this one
 * sends the change as the person's next line, naming the option's id, and
 * the agent calls make_images with it.
 *
 * See api/services/images/service.py for the row this renders.
 */

import { Download, Loader2, Pencil } from 'lucide-react';
import { useEffect, useState } from 'react';

import {
    downloadImageApiV1ImagesImageUuidFileGet,
    imageUrlApiV1ImagesImageUuidGet,
} from '@/client/sdk.gen';
import type { ImageView, TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';

export type ImagesMade = {
    images?: ImageView[];
    provider_label?: string;
    format_label?: string;
    target?: string;
    note?: string;
    edit_of?: string | null;
};

export function imagesOf(event: TimelineEvent): ImagesMade {
    return (event.payload ?? {}) as ImagesMade;
}

/** The line an edit sends: the option and its id, then the change. */
export function editLine(image: ImageView, change: string): string {
    return `Edit option ${image.option_index + 1} (${image.image_uuid}): ${change.trim()}`;
}

function Tile({
    image,
    onEdit,
}: {
    image: ImageView;
    onEdit?: (line: string) => void | Promise<void>;
}) {
    const { user, loading: authLoading } = useAuth();
    const [url, setUrl] = useState<string | null>(null);
    const [failed, setFailed] = useState<string | null>(null);
    const [editing, setEditing] = useState(false);
    const [change, setChange] = useState('');
    const [sending, setSending] = useState(false);
    const [downloading, setDownloading] = useState(false);
    const label = `Option ${image.option_index + 1}`;

    useEffect(() => {
        if (authLoading || !user) return;
        let live = true;
        void (async () => {
            const response = await imageUrlApiV1ImagesImageUuidGet({
                path: { image_uuid: image.image_uuid },
            });
            if (!live) return;
            if (response.error || !response.data) {
                setFailed(detailFromError(response.error, 'Could not show this image'));
                return;
            }
            setUrl(response.data.url);
        })();
        return () => {
            live = false;
        };
    }, [authLoading, user, image.image_uuid]);

    const download = async () => {
        setDownloading(true);
        setFailed(null);
        const response = await downloadImageApiV1ImagesImageUuidFileGet({
            path: { image_uuid: image.image_uuid },
            parseAs: 'blob',
        });
        setDownloading(false);
        if (response.error || !response.data) {
            setFailed(detailFromError(response.error, 'Could not download it'));
            return;
        }
        const href = URL.createObjectURL(response.data as Blob);
        const link = document.createElement('a');
        link.href = href;
        const extension = image.mime_type === 'image/jpeg' ? 'jpg' : image.mime_type === 'image/webp' ? 'webp' : 'png';
        link.download = `${image.format || 'image'}-${image.option_index + 1}.${extension}`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(href);
    };

    const sendEdit = async () => {
        if (!onEdit || !change.trim()) return;
        setSending(true);
        await onEdit(editLine(image, change));
        setSending(false);
        setChange('');
        setEditing(false);
    };

    return (
        <li className="flex flex-col gap-2 rounded-md border border-border p-2" data-testid="image-option">
            <div className="flex aspect-square items-center justify-center overflow-hidden rounded bg-muted">
                {url ? (
                    // A short-lived signed URL from the workspace's own
                    // bucket; next/image would proxy and cache it past expiry.
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={url} alt={label} className="h-full w-full object-contain" />
                ) : failed ? (
                    <span className="p-2 text-center text-xs text-muted-foreground">{label}</span>
                ) : (
                    <Loader2 aria-label={`Loading ${label}`} className="motion-continuous h-5 w-5 animate-spin text-muted-foreground" />
                )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
                <span className="mr-auto text-xs text-muted-foreground">{label}</span>
                <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => void download()}
                    disabled={downloading}
                    aria-label={`Download ${label}`}
                >
                    <Download aria-hidden className="mr-1 h-3.5 w-3.5" />
                    Download
                </Button>
                {onEdit && (
                    <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => setEditing((v) => !v)}
                        aria-expanded={editing}
                        aria-label={`Edit ${label}`}
                    >
                        <Pencil aria-hidden className="mr-1 h-3.5 w-3.5" />
                        Edit this one
                    </Button>
                )}
            </div>
            {editing && (
                <form
                    className="flex gap-2"
                    onSubmit={(e) => {
                        e.preventDefault();
                        void sendEdit();
                    }}
                >
                    <Input
                        aria-label={`What to change on ${label}`}
                        placeholder="Make the headline bigger"
                        value={change}
                        onChange={(e) => setChange(e.target.value)}
                    />
                    <Button type="submit" size="sm" disabled={sending || !change.trim()}>
                        {sending ? 'Sending…' : 'Send'}
                    </Button>
                </form>
            )}
            {failed && (
                <p role="alert" className="text-xs text-destructive">
                    {failed}
                </p>
            )}
        </li>
    );
}

export function ImagesCard({
    event,
    onEdit,
}: {
    event: TimelineEvent;
    /** Sends an edit as the person's next line. Without it, no Edit button. */
    onEdit?: (line: string) => void | Promise<void>;
}) {
    const made = imagesOf(event);
    const images = made.images ?? [];
    const caption = [made.format_label, made.provider_label && `made with ${made.provider_label}`]
        .filter(Boolean)
        .join(' · ');
    return (
        <div className="rounded-lg border border-border bg-card p-3" data-testid="images-card">
            <p className="text-sm font-medium">{event.summary}</p>
            {caption && <p className="text-xs text-muted-foreground">{caption}</p>}
            {made.note && <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">{made.note}</p>}
            {images.length ? (
                <ul className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {images.map((image) => (
                        <Tile key={image.image_uuid} image={image} onEdit={onEdit} />
                    ))}
                </ul>
            ) : (
                // Never an empty grid that looks like a failure to load.
                <p className="mt-2 text-sm text-muted-foreground">No images are on this card.</p>
            )}
        </div>
    );
}
