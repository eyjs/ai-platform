import { Repository } from 'typeorm';
import { KnowledgeService } from './knowledge.service';
import { Document } from '../entities/document.entity';
import { DocumentChunk } from '../entities/document-chunk.entity';
import { JobQueue } from '../entities/job-queue.entity';

function makeDate(): Date {
  return new Date();
}

function makeDocument(overrides: Partial<Document> = {}): Document {
  return {
    id: 'doc-uuid-1',
    title: 'Test Document',
    content: 'Some content',
    source: 'upload',
    status: 'completed',
    filePath: '/tmp/test.pdf',
    fileSize: 1024,
    mimeType: 'application/pdf',
    createdAt: makeDate(),
    updatedAt: makeDate(),
    ...overrides,
  } as Document;
}

function makeQueryBuilder(result: Document[], total: number) {
  const qb = {
    andWhere: jest.fn().mockReturnThis(),
    orderBy: jest.fn().mockReturnThis(),
    skip: jest.fn().mockReturnThis(),
    take: jest.fn().mockReturnThis(),
    getManyAndCount: jest.fn().mockResolvedValue([result, total]),
  };
  return qb;
}

function makeDocumentRepo(overrides: Partial<Record<string, jest.Mock>> = {}): jest.Mocked<Repository<Document>> {
  return {
    findOne: jest.fn(),
    createQueryBuilder: jest.fn(),
    manager: { query: jest.fn() },
    ...overrides,
  } as unknown as jest.Mocked<Repository<Document>>;
}

function makeChunkRepo(): jest.Mocked<Repository<DocumentChunk>> {
  return {
    count: jest.fn(),
  } as unknown as jest.Mocked<Repository<DocumentChunk>>;
}

function makeJobQueueRepo(): jest.Mocked<Repository<JobQueue>> {
  return {
    create: jest.fn(),
    save: jest.fn(),
  } as unknown as jest.Mocked<Repository<JobQueue>>;
}

function makeService(
  documentRepo: jest.Mocked<Repository<Document>>,
  chunkRepo: jest.Mocked<Repository<DocumentChunk>>,
  jobQueueRepo: jest.Mocked<Repository<JobQueue>>,
): KnowledgeService {
  return new KnowledgeService(documentRepo, chunkRepo, jobQueueRepo);
}

describe('KnowledgeService', () => {
  let documentRepo: jest.Mocked<Repository<Document>>;
  let chunkRepo: jest.Mocked<Repository<DocumentChunk>>;
  let jobQueueRepo: jest.Mocked<Repository<JobQueue>>;
  let service: KnowledgeService;

  beforeEach(() => {
    documentRepo = makeDocumentRepo();
    chunkRepo = makeChunkRepo();
    jobQueueRepo = makeJobQueueRepo();
    service = makeService(documentRepo, chunkRepo, jobQueueRepo);
  });

  describe('findDocuments', () => {
    it('returns empty response when no documents', async () => {
      const qb = makeQueryBuilder([], 0);
      documentRepo.createQueryBuilder.mockReturnValue(qb as unknown as ReturnType<typeof documentRepo.createQueryBuilder>);

      const result = await service.findDocuments({});

      expect(result.items).toEqual([]);
      expect(result.total).toBe(0);
      expect(result.page).toBe(1);
      expect(result.size).toBe(20);
    });

    it('returns mapped document items', async () => {
      const doc = makeDocument();
      const qb = makeQueryBuilder([doc], 1);
      documentRepo.createQueryBuilder.mockReturnValue(qb as unknown as ReturnType<typeof documentRepo.createQueryBuilder>);

      const result = await service.findDocuments({});

      expect(result.items).toHaveLength(1);
      expect(result.items[0].id).toBe('doc-uuid-1');
      expect(result.items[0].title).toBe('Test Document');
      expect(result.items[0].status).toBe('completed');
    });

    it('applies status filter when provided', async () => {
      const doc = makeDocument({ status: 'pending' });
      const qb = makeQueryBuilder([doc], 1);
      documentRepo.createQueryBuilder.mockReturnValue(qb as unknown as ReturnType<typeof documentRepo.createQueryBuilder>);

      await service.findDocuments({ status: 'pending' });

      expect(qb.andWhere).toHaveBeenCalledWith('doc.status = :status', { status: 'pending' });
    });

    it('applies source filter when provided', async () => {
      const qb = makeQueryBuilder([], 0);
      documentRepo.createQueryBuilder.mockReturnValue(qb as unknown as ReturnType<typeof documentRepo.createQueryBuilder>);

      await service.findDocuments({ source: 'upload' });

      expect(qb.andWhere).toHaveBeenCalledWith('doc.source = :source', { source: 'upload' });
    });

    it('does not apply filters when not provided', async () => {
      const qb = makeQueryBuilder([], 0);
      documentRepo.createQueryBuilder.mockReturnValue(qb as unknown as ReturnType<typeof documentRepo.createQueryBuilder>);

      await service.findDocuments({});

      expect(qb.andWhere).not.toHaveBeenCalled();
    });

    it('uses pagination params correctly', async () => {
      const qb = makeQueryBuilder([], 0);
      documentRepo.createQueryBuilder.mockReturnValue(qb as unknown as ReturnType<typeof documentRepo.createQueryBuilder>);

      await service.findDocuments({ page: 3, size: 5 });

      // skip = (3-1)*5 = 10
      expect(qb.skip).toHaveBeenCalledWith(10);
      expect(qb.take).toHaveBeenCalledWith(5);
    });

    it('returns correct page and size in response', async () => {
      const qb = makeQueryBuilder([], 0);
      documentRepo.createQueryBuilder.mockReturnValue(qb as unknown as ReturnType<typeof documentRepo.createQueryBuilder>);

      const result = await service.findDocuments({ page: 2, size: 10 });

      expect(result.page).toBe(2);
      expect(result.size).toBe(10);
    });
  });

  describe('findDocumentById', () => {
    it('returns null when document not found', async () => {
      documentRepo.findOne.mockResolvedValue(null);

      const result = await service.findDocumentById('nonexistent');

      expect(result).toBeNull();
    });

    it('returns document detail with chunkCount when found', async () => {
      const doc = makeDocument();
      documentRepo.findOne.mockResolvedValue(doc);
      chunkRepo.count.mockResolvedValue(7);

      const result = await service.findDocumentById('doc-uuid-1');

      expect(result).not.toBeNull();
      expect(result!.id).toBe('doc-uuid-1');
      expect(result!.chunkCount).toBe(7);
      expect(result!.title).toBe('Test Document');
    });

    it('queries chunks by documentId', async () => {
      const doc = makeDocument();
      documentRepo.findOne.mockResolvedValue(doc);
      chunkRepo.count.mockResolvedValue(0);

      await service.findDocumentById('doc-uuid-1');

      expect(chunkRepo.count).toHaveBeenCalledWith({ where: { documentId: 'doc-uuid-1' } });
    });

    it('includes all document fields in detail', async () => {
      const doc = makeDocument({ content: 'detailed content', filePath: '/file.pdf' });
      documentRepo.findOne.mockResolvedValue(doc);
      chunkRepo.count.mockResolvedValue(3);

      const result = await service.findDocumentById('doc-uuid-1');

      expect(result!.content).toBe('detailed content');
      expect(result!.filePath).toBe('/file.pdf');
    });
  });

  describe('getStats', () => {
    it('returns stats with correct structure', async () => {
      const managerQuery = documentRepo.manager.query as jest.Mock;
      managerQuery
        .mockResolvedValueOnce([{
          total_documents: 10,
          pending_documents: 2,
          completed_documents: 7,
          failed_documents: 1,
        }])
        .mockResolvedValueOnce([{ total_chunks: 50 }])
        .mockResolvedValueOnce([
          { status: 'completed', count: 7 },
          { status: 'pending', count: 2 },
          { status: 'failed', count: 1 },
        ])
        .mockResolvedValueOnce([
          { source: 'upload', count: 6 },
          { source: 'api', count: 4 },
        ]);

      const result = await service.getStats();

      expect(result.totalDocuments).toBe(10);
      expect(result.pendingDocuments).toBe(2);
      expect(result.completedDocuments).toBe(7);
      expect(result.failedDocuments).toBe(1);
      expect(result.totalChunks).toBe(50);
      expect(result.avgChunksPerDocument).toBe(5);
    });

    it('computes avgChunksPerDocument as 0 when totalDocuments is 0', async () => {
      const managerQuery = documentRepo.manager.query as jest.Mock;
      managerQuery
        .mockResolvedValueOnce([{ total_documents: 0, pending_documents: 0, completed_documents: 0, failed_documents: 0 }])
        .mockResolvedValueOnce([{ total_chunks: 0 }])
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([]);

      const result = await service.getStats();

      expect(result.avgChunksPerDocument).toBe(0);
    });

    it('handles query failures gracefully', async () => {
      const managerQuery = documentRepo.manager.query as jest.Mock;
      managerQuery.mockRejectedValue(new Error('DB failure'));

      const result = await service.getStats();

      expect(result.totalDocuments).toBe(0);
    });

    it('maps documentsByStatus correctly', async () => {
      const managerQuery = documentRepo.manager.query as jest.Mock;
      managerQuery
        .mockResolvedValueOnce([{ total_documents: 3, pending_documents: 1, completed_documents: 2, failed_documents: 0 }])
        .mockResolvedValueOnce([{ total_chunks: 10 }])
        .mockResolvedValueOnce([{ status: 'completed', count: 2 }, { status: 'pending', count: 1 }])
        .mockResolvedValueOnce([{ source: 'upload', count: 3 }]);

      const result = await service.getStats();

      expect(result.documentsByStatus).toHaveLength(2);
      expect(result.documentsByStatus[0]).toEqual({ status: 'completed', count: 2 });
    });
  });

  describe('requestReindex', () => {
    it('creates and saves a pending reindex job', async () => {
      const doc = makeDocument();
      documentRepo.findOne.mockResolvedValue(doc);
      const job: Partial<JobQueue> = {
        id: 'job-uuid-1',
        jobType: 'reindex_document',
        status: 'pending',
        priority: 10,
      };
      jobQueueRepo.create.mockReturnValue(job as JobQueue);
      jobQueueRepo.save.mockResolvedValue({ ...job, id: 'job-uuid-1' } as JobQueue);

      const result = await service.requestReindex('doc-uuid-1');

      expect(result.jobId).toBe('job-uuid-1');
      expect(result.documentId).toBe('doc-uuid-1');
      expect(result.status).toBe('queued');
      expect(result.message).toContain('Test Document');
    });

    it('throws when document not found', async () => {
      documentRepo.findOne.mockResolvedValue(null);

      await expect(service.requestReindex('nonexistent')).rejects.toThrow('not found');
    });

    it('creates job with correct payload', async () => {
      const doc = makeDocument({ id: 'doc-uuid-1', title: 'My Doc' });
      documentRepo.findOne.mockResolvedValue(doc);
      jobQueueRepo.create.mockReturnValue({ id: 'job-1', jobType: 'reindex_document' } as JobQueue);
      jobQueueRepo.save.mockResolvedValue({ id: 'job-1' } as JobQueue);

      await service.requestReindex('doc-uuid-1');

      expect(jobQueueRepo.create).toHaveBeenCalledWith(
        expect.objectContaining({
          jobType: 'reindex_document',
          payload: { document_id: 'doc-uuid-1', title: 'My Doc' },
          status: 'pending',
          priority: 10,
        }),
      );
    });
  });
});
