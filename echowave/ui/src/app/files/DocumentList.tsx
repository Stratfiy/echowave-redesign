'use client';

import { AlertTriangle, FileText, FolderInput, Languages, Pencil, RefreshCw, Search, SearchX, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

import {
  deleteDocumentApiV1KnowledgeBaseDocumentsDocumentUuidDelete,
  listDocumentsApiV1KnowledgeBaseDocumentsGet,
  translateDocumentRouteApiV1KnowledgeBaseDocumentsDocumentUuidTranslatePost,
  updateDocumentApiV1KnowledgeBaseDocumentsDocumentUuidPatch,
} from '@/client/sdk.gen';
import { getUsageApiV1KnowledgeBaseUsageGet } from "@/client/sdk.gen";
import type { DocumentResponseSchema } from '@/client/types.gen';
import { useConfirm } from "@/components/ConfirmDialog";
import { EmptyState } from '@/components/EmptyState';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { detailFromError } from '@/lib/apiError';
import logger from '@/lib/logger';

import { FILE_DRAG_TYPE, type FileFolder } from './fileFolders';
import { FolderPicker } from './FolderBrowser';

/** The languages a copy can be made in. Mirrors LANGUAGE_NAMES in
 *  api/services/knowledge_base/translate_document.py. */
export const TRANSLATION_LANGUAGES: { code: string; name: string }[] = [
  { code: 'en-IN', name: 'English' },
  { code: 'hi-IN', name: 'Hindi' },
  { code: 'ta-IN', name: 'Tamil' },
  { code: 'te-IN', name: 'Telugu' },
  { code: 'kn-IN', name: 'Kannada' },
  { code: 'ml-IN', name: 'Malayalam' },
  { code: 'mr-IN', name: 'Marathi' },
  { code: 'bn-IN', name: 'Bengali' },
  { code: 'gu-IN', name: 'Gujarati' },
  { code: 'pa-IN', name: 'Punjabi' },
  { code: 'od-IN', name: 'Odia' },
];

interface DocumentListProps {
  refreshTrigger: number;
  /** The Files-page folder being shown; null is the top level. Left out,
   *  every file is listed (the list's behaviour before folders). */
  fileFolderId?: number | null;
  /** Every folder, for "Move to…". */
  folders?: readonly FileFolder[];
  /** A file was renamed, moved or deleted: folder counts may have changed. */
  onChanged?: () => void;
}

export default function DocumentList({ refreshTrigger, fileFolderId, folders = [], onChanged }: DocumentListProps) {
  const [documents, setDocuments] = useState<DocumentResponseSchema[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const { confirm, dialog: confirmDialog } = useConfirm();

  // How much of the allowance is spent. Shown before somebody hits the wall:
  // a cap a customer cannot see is indistinguishable from a bug the first time
  // it refuses them.
  const [usage, setUsage] = useState<{
    bytes_used: number;
    bytes_limit: number;
  } | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<DocumentResponseSchema | null>(null);
  const [newName, setNewName] = useState('');
  const [renameError, setRenameError] = useState<string | null>(null);
  const [moving, setMoving] = useState<DocumentResponseSchema | null>(null);

  const fetchDocuments = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);

      const response = await listDocumentsApiV1KnowledgeBaseDocumentsGet({
        query: {
          limit: 100,
          offset: 0,
          ...(fileFolderId === undefined
            ? {}
            : fileFolderId === null
              ? { top_level: true }
              : { file_folder_id: fileFolderId }),
        },
      });

      if (response.error || !response.data) {
        throw new Error(detailFromError(response.error, 'Could not load the files'));
      }

      setDocuments(response.data.documents);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch documents');
      logger.error('Error fetching documents:', err);
    } finally {
      setIsLoading(false);
    }
  }, [fileFolderId]);

  // Fetch documents on mount and when refreshTrigger changes
  useEffect(() => {
    void (async () => {
      const response = await getUsageApiV1KnowledgeBaseUsageGet();
      // A failure here costs the reader a progress bar, not the screen.
      if (response.data) {
        setUsage(response.data as unknown as { bytes_used: number; bytes_limit: number });
      }
    })();
  }, [documents.length]);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments, refreshTrigger]);

  // Poll for documents that are processing
  useEffect(() => {
    const processingDocs = documents.filter(
      (doc) => doc.processing_status === 'processing' || doc.processing_status === 'pending'
    );

    if (processingDocs.length === 0) return;

    const pollInterval = setInterval(() => {
      logger.info(`Polling for ${processingDocs.length} processing documents...`);
      fetchDocuments();
    }, 5000); // Poll every 5 seconds

    return () => clearInterval(pollInterval);
  }, [documents, fetchDocuments]);

  const handleDelete = async (documentUuid: string, filename: string) => {
    const ok = await confirm({
      title: `Delete "${filename}"?`,
      description:
        "The file and everything indexed from it are removed. Agents that were answering from it will stop being able to. This cannot be undone.",
      confirmLabel: "Delete file",
      destructive: true,
    });
    if (!ok) return;

    try {
      const response = await deleteDocumentApiV1KnowledgeBaseDocumentsDocumentUuidDelete({
        path: {
          document_uuid: documentUuid,
        },
      });

      if (response.error) {
        throw new Error(detailFromError(response.error, 'Could not delete the file'));
      }

      toast.success(`Deleted "${filename}"`);
      fetchDocuments();
      onChanged?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete document');
      logger.error('Error deleting document:', err);
    }
  };

  const saveRename = async () => {
    if (!renaming) return;
    const response = await updateDocumentApiV1KnowledgeBaseDocumentsDocumentUuidPatch({
      path: { document_uuid: renaming.document_uuid },
      body: { filename: newName },
    });
    if (response.error) {
      setRenameError(detailFromError(response.error, 'Could not rename the file'));
      return;
    }
    setRenaming(null);
    toast.success(`Renamed to "${response.data?.filename ?? newName}"`);
    fetchDocuments();
  };

  const moveTo = async (doc: DocumentResponseSchema, target: number | null) => {
    const response = await updateDocumentApiV1KnowledgeBaseDocumentsDocumentUuidPatch({
      path: { document_uuid: doc.document_uuid },
      body: { file_folder_id: target },
    });
    if (response.error) {
      toast.error(detailFromError(response.error, 'Could not move the file'));
      return;
    }
    setMoving(null);
    const where = target == null ? 'All files' : folders.find((f) => f.id === target)?.path ?? 'the folder';
    toast.success(`Moved "${doc.filename}" to ${where}`);
    fetchDocuments();
    onChanged?.();
  };

  // A copy in another language, as a document of its own in the same scope.
  // Offered on a document that has been read; the copy shows up pending and
  // the list's own polling carries it to completed.
  const handleTranslate = async (documentUuid: string, filename: string, code: string, name: string) => {
    const response = await translateDocumentRouteApiV1KnowledgeBaseDocumentsDocumentUuidTranslatePost({
      path: { document_uuid: documentUuid },
      body: { target_language_code: code },
    });
    if (response.error) {
      toast.error(detailFromError(response.error, 'Could not start the translation'));
      return;
    }
    toast.success(`Making a ${name} copy of "${filename}"`);
    fetchDocuments();
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'completed':
        return <Badge className="bg-green-500">Completed</Badge>;
      case 'processing':
        return (
          <Badge variant="secondary" className="animate-pulse">
            Processing
          </Badge>
        );
      case 'pending':
        return <Badge variant="outline">Pending</Badge>;
      case 'failed':
        return <Badge variant="destructive">Failed</Badge>;
      default:
        return <Badge variant="outline">{status}</Badge>;
    }
  };

  // Who reads it. The scope is the one fact on this screen that changes what
  // a bot does, so it is said on every row rather than hidden in a filter.
  const whoReads = (doc: DocumentResponseSchema): string => {
    switch (doc.scope) {
      case 'org':
        return 'Every agent';
      case 'channel':
        return 'One channel';
      case 'bot':
        return 'One agent';
      default:
        return 'Steps that name it';
    }
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
  };

  const formatDate = (dateString: string): string => {
    const date = new Date(dateString);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
  };

  const filteredDocuments = documents.filter((doc) =>
    doc.filename.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // Counted across every document, not just the filtered view: a search box
  // with something typed in it must not make the warning disappear.
  const strandedCount = documents.filter((doc) => doc.needs_reingest).length;

  if (isLoading && documents.length === 0) {
    return (
      <div className="space-y-4">
        {[1, 2, 3].map((i) => (
          <div key={i} className="flex items-center justify-between p-4 border rounded-lg">
            <div className="space-y-2 flex-1">
              <Skeleton className="h-4 w-48" />
              <Skeleton className="h-3 w-full max-w-64" />
            </div>
            <Skeleton className="h-8 w-24" />
          </div>
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-lg text-destructive">
        {error}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {confirmDialog}
      {/* The failure this exists for is silent: every document below still says
          "Completed", and the agent retrieves nothing from any of them, because
          they were embedded with a model the organization has since moved off.
          Nothing else on this screen would tell anyone. */}
      {usage && usage.bytes_limit > 0 && (
        <div className="rounded-lg border p-4">
          <div className="flex items-baseline justify-between gap-4 text-sm">
            <span className="font-medium">Knowledge base storage</span>
            <span className="text-muted-foreground tabular-nums">
              {formatFileSize(usage.bytes_used)} of{" "}
              {formatFileSize(usage.bytes_limit)}
            </span>
          </div>
          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className={
                usage.bytes_used / usage.bytes_limit > 0.9
                  ? "h-full bg-destructive"
                  : "h-full bg-primary"
              }
              style={{
                width: `${Math.min(100, (usage.bytes_used / usage.bytes_limit) * 100)}%`,
              }}
            />
          </div>
          {usage.bytes_used / usage.bytes_limit > 0.9 && (
            <p className="mt-2 text-xs text-muted-foreground">
              Nearly full. Delete a document to make room, or contact support to
              raise the limit.
            </p>
          )}
        </div>
      )}

      {strandedCount > 0 && (
        <div className="flex items-start gap-3 p-4 rounded-lg border border-amber-500/30 bg-amber-500/10">
          <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
          <div className="text-sm">
            <p className="font-medium">
              {strandedCount === 1
                ? '1 document needs re-ingesting'
                : `${strandedCount} documents need re-ingesting`}
            </p>
            <p className="text-muted-foreground mt-1">
              These were processed with a different embedding model than the one
              your organization uses now, so your agent cannot retrieve anything
              from them. Delete and re-upload them to fix it. Re-ingesting calls
              your embedding provider again, so it is charged to your account —
              which is why it does not happen on its own.
            </p>
          </div>
        </div>
      )}

      {/* Search and Refresh */}
      <div className="flex items-center gap-4">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder={fileFolderId === undefined ? 'Search documents...' : 'Search this folder...'}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-10"
          />
        </div>
        <Button
          variant="outline"
          size="icon"
          onClick={fetchDocuments}
          disabled={isLoading}
        >
          <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {/* Document List */}
      {filteredDocuments.length === 0 ? (
        searchQuery ? (
          <EmptyState
            icon={SearchX}
            title="No documents match your search"
            description="Try a shorter search, or clear it to see everything."
          />
        ) : (
          <EmptyState
            icon={FileText}
            art="folder"
            title={fileFolderId ? 'This folder is empty' : 'No documents yet'}
            description={
              fileFolderId
                ? 'Drop files here, or drag a file from another folder onto this one.'
                : 'Upload your price list, policy or FAQ and the agent can answer from it during a call, in its own words.'
            }
          />
        )
      ) : (
        <div className="space-y-3">
          {filteredDocuments.map((doc) => (
            <div
              key={doc.document_uuid}
              draggable
              onDragStart={(event) => {
                event.dataTransfer.setData(FILE_DRAG_TYPE, doc.document_uuid);
                event.dataTransfer.setData('text/plain', doc.filename);
                event.dataTransfer.effectAllowed = 'move';
              }}
              data-testid={`file-${doc.document_uuid}`}
              className="flex cursor-grab items-center justify-between p-4 border rounded-lg hover:bg-muted/50 transition-colors active:cursor-grabbing"
            >
              <div className="flex items-center gap-4 flex-1">
                <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center">
                  <FileText className="w-5 h-5 text-primary" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium truncate">{doc.filename}</span>
                    {getStatusBadge(doc.processing_status)}
                    {doc.needs_reingest && (
                      <Badge
                        variant="outline"
                        className="text-xs border-amber-500/40 text-amber-700 dark:text-amber-500"
                        title="Embedded with a model your organization no longer uses. The agent cannot retrieve from this document until it is re-ingested."
                      >
                        Needs re-ingesting
                      </Badge>
                    )}
                    <Badge variant="secondary" className="text-xs">{whoReads(doc)}</Badge>
                    {doc.retrieval_mode === 'full_document' ? (
                      <Badge variant="outline" className="text-xs">Full Document</Badge>
                    ) : (
                      <Badge variant="outline" className="text-xs">Chunked</Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-4 text-sm text-muted-foreground">
                    <span>{formatFileSize(doc.file_size_bytes)}</span>
                    {doc.processing_status === 'completed' && doc.retrieval_mode !== 'full_document' && (
                      <span>{doc.total_chunks} chunks</span>
                    )}
                    <span>{formatDate(doc.created_at)}</span>
                  </div>
                  {doc.processing_error && (
                    <p className="text-xs text-destructive mt-1">
                      Error: {doc.processing_error}
                    </p>
                  )}
                  {typeof doc.custom_metadata?.translated_from === 'string' && (
                    <p className="text-xs text-muted-foreground mt-1">
                      A translation of another document here
                    </p>
                  )}
                  {doc.docling_metadata &&
                   typeof doc.docling_metadata === 'object' &&
                   'duplicate_of' in doc.docling_metadata && (
                    <p className="text-xs text-muted-foreground mt-1">
                      Duplicate of another document
                    </p>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Rename ${doc.filename}`}
                  title="Rename"
                  onClick={() => {
                    setRenaming(doc);
                    setNewName(doc.filename);
                    setRenameError(null);
                  }}
                >
                  <Pencil className="h-4 w-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Move ${doc.filename}`}
                  title="Move to…"
                  onClick={() => setMoving(doc)}
                >
                  <FolderInput className="h-4 w-4" />
                </Button>
                {doc.processing_status === 'completed' && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="sm" aria-label={`Translate ${doc.filename}`} title="Translate">
                        <Languages className="h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-44">
                      <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
                        Make a copy in
                      </DropdownMenuLabel>
                      {TRANSLATION_LANGUAGES.map((language) => (
                        <DropdownMenuItem
                          key={language.code}
                          onSelect={() => void handleTranslate(doc.document_uuid, doc.filename, language.code, language.name)}
                        >
                          {language.name}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Delete ${doc.filename}`}
                  onClick={() => handleDelete(doc.document_uuid, doc.filename)}
                  className="text-destructive hover:text-destructive/90"
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <Dialog open={renaming != null} onOpenChange={(open) => !open && setRenaming(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename file</DialogTitle>
            <DialogDescription>Agents cite it by its new name straight away.</DialogDescription>
          </DialogHeader>
          <form
            className="space-y-2"
            onSubmit={(event) => {
              event.preventDefault();
              void saveRename();
            }}
          >
            <Input autoFocus aria-label="File name" value={newName} onChange={(event) => setNewName(event.target.value)} />
            {renameError && <p className="text-sm text-destructive">{renameError}</p>}
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setRenaming(null)}>
                Cancel
              </Button>
              <Button type="submit" disabled={!newName.trim()}>
                Rename
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={moving != null} onOpenChange={(open) => !open && setMoving(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Move {moving?.filename}</DialogTitle>
            <DialogDescription>Folders only organise: every agent that reads it now still will.</DialogDescription>
          </DialogHeader>
          {moving && (
            <FolderPicker
              folders={folders}
              current={moving.file_folder_id ?? null}
              onPick={(target) => void moveTo(moving, target)}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
