'use client';

import { AlertTriangle, FileText, RefreshCw, Search, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

import {
  deleteDocumentApiV1KnowledgeBaseDocumentsDocumentUuidDelete,
  listDocumentsApiV1KnowledgeBaseDocumentsGet,
} from '@/client/sdk.gen';
import { getUsageApiV1KnowledgeBaseUsageGet } from "@/client/sdk.gen";
import type { DocumentResponseSchema } from '@/client/types.gen';
import { useConfirm } from "@/components/ConfirmDialog";
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import logger from '@/lib/logger';

interface DocumentListProps {
  refreshTrigger: number;
}

export default function DocumentList({ refreshTrigger }: DocumentListProps) {
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

  const fetchDocuments = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);

      const response = await listDocumentsApiV1KnowledgeBaseDocumentsGet({
        query: {
          limit: 100,
          offset: 0,
        },
      });

      if (response.error || !response.data) {
        throw new Error('Failed to fetch documents');
      }

      setDocuments(response.data.documents);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch documents');
      logger.error('Error fetching documents:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

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
        throw new Error('Failed to delete document');
      }

      toast.success(`Deleted "${filename}"`);
      fetchDocuments();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete document');
      logger.error('Error deleting document:', err);
    }
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
              <Skeleton className="h-3 w-64" />
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
            placeholder="Search documents..."
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
        <div className="text-center py-12">
          <FileText className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
          <p className="text-muted-foreground">
            {searchQuery
              ? 'No documents match your search'
              : 'No documents uploaded yet'}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {filteredDocuments.map((doc) => (
            <div
              key={doc.document_uuid}
              className="flex items-center justify-between p-4 border rounded-lg hover:bg-muted/50 transition-colors"
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
                  {doc.docling_metadata &&
                   typeof doc.docling_metadata === 'object' &&
                   'duplicate_of' in doc.docling_metadata && (
                    <p className="text-xs text-muted-foreground mt-1">
                      Duplicate of another document
                    </p>
                  )}
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => handleDelete(doc.document_uuid, doc.filename)}
                className="text-destructive hover:text-destructive/90"
              >
                <Trash2 className="w-4 h-4" />
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
