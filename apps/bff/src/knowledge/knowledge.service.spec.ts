import { Repository } from 'typeorm';
import { KnowledgeService } from './knowledge.service';
import { Document } from '../entities/document.entity';
import { DocumentChunk } from '../entities/document-chunk.entity';

function makeDate(): Date {
  return new Date();
}

function makeDocument(overrides: Partial<Document> = {}): Document {
  return {
    id: 'doc-uuid-1',
    title: 'Test Document',
    fileName: 'test.pdf',
    domainCode: '사주명리',
    securityLevel: 'PUBLIC',
    sourceUrl: null,
    metadata: null,
    createdAt: makeDate(),
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

function makeService(
  documentRepo: jest.Mocked<Repository<Document>>,
  chunkRepo: jest.Mocked<Repository<DocumentChunk>>,
): KnowledgeService {
  return new KnowledgeService(documentRepo, chunkRepo);
}

describe('KnowledgeService', () => {
  let documentRepo: jest.Mocked<Repository<Document>>;
  let chunkRepo: jest.Mocked<Repository<DocumentChunk>>;
  let service: KnowledgeService;

  beforeEach(() => {
    documentRepo = makeDocumentRepo();
    chunkRepo = makeChunkRepo();
    service = makeService(documentRepo, chunkRepo);
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
      expect(result.items[0].domainCode).toBe('사주명리');
    });

    it('applies domainCode filter when provided', async () => {
      const doc = makeDocument({ domainCode: '사주명리' });
      const qb = makeQueryBuilder([doc], 1);
      documentRepo.createQueryBuilder.mockReturnValue(qb as unknown as ReturnType<typeof documentRepo.createQueryBuilder>);

      await service.findDocuments({ domainCode: '사주명리' });

      expect(qb.andWhere).toHaveBeenCalledWith('doc.domainCode = :domainCode', { domainCode: '사주명리' });
    });

    it('applies securityLevel filter when provided', async () => {
      const qb = makeQueryBuilder([], 0);
      documentRepo.createQueryBuilder.mockReturnValue(qb as unknown as ReturnType<typeof documentRepo.createQueryBuilder>);

      await service.findDocuments({ securityLevel: 'PUBLIC' });

      expect(qb.andWhere).toHaveBeenCalledWith('doc.securityLevel = :securityLevel', { securityLevel: 'PUBLIC' });
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
      (documentRepo.manager.query as jest.Mock).mockResolvedValue([]);

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
      (documentRepo.manager.query as jest.Mock).mockResolvedValue([]);

      await service.findDocumentById('doc-uuid-1');

      expect(chunkRepo.count).toHaveBeenCalledWith({ where: { documentId: 'doc-uuid-1' } });
    });

    it('assembles content from chunks', async () => {
      const doc = makeDocument();
      documentRepo.findOne.mockResolvedValue(doc);
      chunkRepo.count.mockResolvedValue(2);
      (documentRepo.manager.query as jest.Mock).mockResolvedValue([
        { content: 'chunk1' },
        { content: 'chunk2' },
      ]);

      const result = await service.findDocumentById('doc-uuid-1');

      expect(result!.content).toBe('chunk1\n\nchunk2');
      expect(result!.domainCode).toBe('사주명리');
    });
  });

  describe('getStats', () => {
    it('returns stats with correct structure', async () => {
      const managerQuery = documentRepo.manager.query as jest.Mock;
      managerQuery
        .mockResolvedValueOnce([{ total_documents: 10 }])
        .mockResolvedValueOnce([{ total_chunks: 50 }])
        .mockResolvedValueOnce([
          { domain: '사주명리', count: 6 },
          { domain: '보험', count: 4 },
        ])
        .mockResolvedValueOnce([{ level: 'PUBLIC', count: 10 }]);

      const result = await service.getStats();

      expect(result.totalDocuments).toBe(10);
      expect(result.totalChunks).toBe(50);
      expect(result.avgChunksPerDocument).toBe(5);
      expect(result.documentsByDomain).toHaveLength(2);
      expect(result.documentsBySecurityLevel[0]).toEqual({ level: 'PUBLIC', count: 10 });
    });

    it('computes avgChunksPerDocument as 0 when totalDocuments is 0', async () => {
      const managerQuery = documentRepo.manager.query as jest.Mock;
      managerQuery
        .mockResolvedValueOnce([{ total_documents: 0 }])
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

    it('maps documentsByDomain correctly', async () => {
      const managerQuery = documentRepo.manager.query as jest.Mock;
      managerQuery
        .mockResolvedValueOnce([{ total_documents: 3 }])
        .mockResolvedValueOnce([{ total_chunks: 10 }])
        .mockResolvedValueOnce([{ domain: '사주명리', count: 2 }, { domain: '보험', count: 1 }])
        .mockResolvedValueOnce([{ level: 'PUBLIC', count: 3 }]);

      const result = await service.getStats();

      expect(result.documentsByDomain).toHaveLength(2);
      expect(result.documentsByDomain[0]).toEqual({ domain: '사주명리', count: 2 });
    });
  });

});
