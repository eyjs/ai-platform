import { NotFoundException, BadRequestException } from '@nestjs/common';
import { Repository, DataSource } from 'typeorm';
import { ApiKeysService } from './api-keys.service';
import { ApiKey } from '../entities/api-key.entity';
import { ApiKeyAuditLog } from '../entities/api-key-audit-log.entity';

function makeDate(): Date {
  return new Date();
}

function makeApiKey(overrides: Partial<ApiKey> = {}): ApiKey {
  return {
    id: 'key-uuid-1',
    keyHash: 'a'.repeat(64),
    name: 'Test Key',
    userId: 'user-1',
    userRole: 'VIEWER',
    securityLevelMax: 'PUBLIC',
    allowedProfiles: [],
    rateLimitPerMin: 60,
    rateLimitPerDay: 10000,
    isActive: true,
    expiresAt: null,
    rotatedFromId: null,
    revokedAt: null,
    createdAt: makeDate(),
    lastUsedAt: null,
    ...overrides,
  } as ApiKey;
}

function makeKeyRepo(): jest.Mocked<Repository<ApiKey>> {
  return {
    find: jest.fn(),
    findOne: jest.fn(),
    save: jest.fn(),
    create: jest.fn(),
  } as unknown as jest.Mocked<Repository<ApiKey>>;
}

function makeAuditRepo(): jest.Mocked<Repository<ApiKeyAuditLog>> {
  return {
    find: jest.fn(),
    save: jest.fn(),
    create: jest.fn(),
  } as unknown as jest.Mocked<Repository<ApiKeyAuditLog>>;
}

function makeDataSource(): jest.Mocked<DataSource> {
  const mockEm = {
    findOne: jest.fn(),
    save: jest.fn(),
    create: jest.fn(),
  };
  return {
    transaction: jest.fn().mockImplementation(async (cb: (em: typeof mockEm) => Promise<unknown>) => cb(mockEm)),
    _mockEm: mockEm,
  } as unknown as jest.Mocked<DataSource>;
}

function makeService(
  keyRepo: jest.Mocked<Repository<ApiKey>>,
  auditRepo: jest.Mocked<Repository<ApiKeyAuditLog>>,
  dataSource: jest.Mocked<DataSource>,
): ApiKeysService {
  return new ApiKeysService(keyRepo, auditRepo, dataSource);
}

describe('ApiKeysService', () => {
  let keyRepo: jest.Mocked<Repository<ApiKey>>;
  let auditRepo: jest.Mocked<Repository<ApiKeyAuditLog>>;
  let dataSource: jest.Mocked<DataSource>;
  let service: ApiKeysService;

  beforeEach(() => {
    keyRepo = makeKeyRepo();
    auditRepo = makeAuditRepo();
    dataSource = makeDataSource();
    service = makeService(keyRepo, auditRepo, dataSource);
  });

  describe('list', () => {
    it('returns empty array when no keys exist', async () => {
      keyRepo.find.mockResolvedValue([]);

      const result = await service.list();

      expect(result).toEqual([]);
      expect(keyRepo.find).toHaveBeenCalledWith({ order: { createdAt: 'DESC' } });
    });

    it('returns mapped response DTOs for all keys', async () => {
      const key = makeApiKey();
      keyRepo.find.mockResolvedValue([key]);

      const result = await service.list();

      expect(result).toHaveLength(1);
      expect(result[0].id).toBe('key-uuid-1');
      expect(result[0].name).toBe('Test Key');
      expect(result[0].is_active).toBe(true);
    });

    it('formats preview with last 8 chars of hash', async () => {
      const key = makeApiKey({ keyHash: 'abc'.padStart(64, '0') });
      keyRepo.find.mockResolvedValue([key]);

      const result = await service.list();

      expect(result[0].preview).toBe(`aip_****${key.keyHash.slice(-8)}`);
    });
  });

  describe('findOne', () => {
    it('returns response DTO when key exists', async () => {
      const key = makeApiKey();
      keyRepo.findOne.mockResolvedValue(key);

      const result = await service.findOne('key-uuid-1');

      expect(result.id).toBe('key-uuid-1');
    });

    it('throws NotFoundException when key not found', async () => {
      keyRepo.findOne.mockResolvedValue(null);

      await expect(service.findOne('nonexistent')).rejects.toThrow(NotFoundException);
    });
  });

  describe('getAudit', () => {
    it('returns empty array when no audit logs', async () => {
      auditRepo.find.mockResolvedValue([]);

      const result = await service.getAudit('key-uuid-1');

      expect(result).toEqual([]);
    });

    it('returns mapped audit entries', async () => {
      const log: ApiKeyAuditLog = {
        id: 'audit-1',
        apiKeyId: 'key-uuid-1',
        actor: 'admin',
        action: 'create',
        before: null,
        after: { name: 'Test Key' },
        createdAt: makeDate(),
      };
      auditRepo.find.mockResolvedValue([log]);

      const result = await service.getAudit('key-uuid-1');

      expect(result).toHaveLength(1);
      expect(result[0].action).toBe('create');
      expect(result[0].api_key_id).toBe('key-uuid-1');
    });

    it('caps limit at 500', async () => {
      auditRepo.find.mockResolvedValue([]);

      await service.getAudit('key-uuid-1', 1000);

      expect(auditRepo.find).toHaveBeenCalledWith(
        expect.objectContaining({ take: 500 }),
      );
    });
  });

  describe('create', () => {
    it('generates base58 key with aip_ prefix', async () => {
      const key = makeApiKey();
      keyRepo.create.mockReturnValue(key);
      keyRepo.save.mockResolvedValue(key);
      auditRepo.create.mockReturnValue({} as ApiKeyAuditLog);
      auditRepo.save.mockResolvedValue({} as ApiKeyAuditLog);

      const result = await service.create(
        {
          name: 'New Key',
          allowed_profiles: [],
          rate_limit_per_min: 60,
          rate_limit_per_day: 1000,
          security_level_max: 'PUBLIC',
          expires_at: null,
        },
        'admin',
      );

      expect(result.plaintext_key).toMatch(/^aip_/);
      expect(result.id).toBeDefined();
    });

    it('writes create audit log', async () => {
      const key = makeApiKey();
      keyRepo.create.mockReturnValue(key);
      keyRepo.save.mockResolvedValue(key);
      auditRepo.create.mockReturnValue({} as ApiKeyAuditLog);
      auditRepo.save.mockResolvedValue({} as ApiKeyAuditLog);

      await service.create(
        { name: 'K', allowed_profiles: [], rate_limit_per_min: 60, rate_limit_per_day: 1000, security_level_max: 'PUBLIC', expires_at: null },
        'admin',
      );

      expect(auditRepo.create).toHaveBeenCalledWith(
        expect.objectContaining({ action: 'create' }),
      );
    });

    it('sets expiresAt from string when provided', async () => {
      const key = makeApiKey();
      keyRepo.create.mockReturnValue(key);
      keyRepo.save.mockResolvedValue(key);
      auditRepo.create.mockReturnValue({} as ApiKeyAuditLog);
      auditRepo.save.mockResolvedValue({} as ApiKeyAuditLog);

      await service.create(
        { name: 'K', allowed_profiles: [], rate_limit_per_min: 60, rate_limit_per_day: 1000, security_level_max: 'PUBLIC', expires_at: '2030-01-01T00:00:00Z' },
        'admin',
      );

      expect(keyRepo.create).toHaveBeenCalledWith(
        expect.objectContaining({ expiresAt: new Date('2030-01-01T00:00:00Z') }),
      );
    });
  });

  describe('update', () => {
    it('updates key fields and writes audit log', async () => {
      const existing = makeApiKey();
      keyRepo.findOne.mockResolvedValue(existing);
      keyRepo.save.mockResolvedValue({ ...existing, name: 'Updated' } as ApiKey);
      auditRepo.create.mockReturnValue({} as ApiKeyAuditLog);
      auditRepo.save.mockResolvedValue({} as ApiKeyAuditLog);

      const result = await service.update('key-uuid-1', { name: 'Updated' }, 'admin');

      expect(keyRepo.save).toHaveBeenCalled();
      expect(auditRepo.create).toHaveBeenCalledWith(
        expect.objectContaining({ action: 'update' }),
      );
      expect(result.name).toBe('Updated');
    });

    it('throws NotFoundException when key not found', async () => {
      keyRepo.findOne.mockResolvedValue(null);

      await expect(service.update('nonexistent', {}, 'admin')).rejects.toThrow(NotFoundException);
    });

    it('ignores undefined fields in update', async () => {
      const existing = makeApiKey({ name: 'Original' });
      keyRepo.findOne.mockResolvedValue(existing);
      keyRepo.save.mockResolvedValue(existing);
      auditRepo.create.mockReturnValue({} as ApiKeyAuditLog);
      auditRepo.save.mockResolvedValue({} as ApiKeyAuditLog);

      await service.update('key-uuid-1', {}, 'admin');

      expect(keyRepo.save).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'Original' }),
      );
    });
  });

  describe('revoke', () => {
    it('sets isActive to false and revokedAt', async () => {
      const key = makeApiKey({ isActive: true, revokedAt: null });
      keyRepo.findOne.mockResolvedValue(key);
      keyRepo.save.mockResolvedValue({ ...key, isActive: false, revokedAt: new Date() } as ApiKey);
      auditRepo.create.mockReturnValue({} as ApiKeyAuditLog);
      auditRepo.save.mockResolvedValue({} as ApiKeyAuditLog);

      const result = await service.revoke('key-uuid-1', 'admin');

      expect(keyRepo.save).toHaveBeenCalledWith(
        expect.objectContaining({ isActive: false }),
      );
      expect(result.is_active).toBe(false);
    });

    it('throws NotFoundException when key not found', async () => {
      keyRepo.findOne.mockResolvedValue(null);

      await expect(service.revoke('nonexistent', 'admin')).rejects.toThrow(NotFoundException);
    });

    it('throws BadRequestException when key is already revoked', async () => {
      const key = makeApiKey({ isActive: false, revokedAt: new Date() });
      keyRepo.findOne.mockResolvedValue(key);

      await expect(service.revoke('key-uuid-1', 'admin')).rejects.toThrow(BadRequestException);
    });

    it('writes revoke audit log', async () => {
      const key = makeApiKey({ isActive: true, revokedAt: null });
      keyRepo.findOne.mockResolvedValue(key);
      keyRepo.save.mockResolvedValue({ ...key, isActive: false, revokedAt: new Date() } as ApiKey);
      auditRepo.create.mockReturnValue({} as ApiKeyAuditLog);
      auditRepo.save.mockResolvedValue({} as ApiKeyAuditLog);

      await service.revoke('key-uuid-1', 'admin');

      expect(auditRepo.create).toHaveBeenCalledWith(
        expect.objectContaining({ action: 'revoke' }),
      );
    });
  });

  describe('rotate', () => {
    it('revokes old key and creates new key in transaction', async () => {
      const oldKey = makeApiKey({ id: 'old-key-id', isActive: true });
      const newKey = makeApiKey({ id: 'new-key-id', rotatedFromId: 'old-key-id' });

      const mockEm = (dataSource as unknown as { _mockEm: { findOne: jest.Mock; save: jest.Mock; create: jest.Mock } })._mockEm;
      mockEm.findOne.mockResolvedValue(oldKey);
      mockEm.save
        .mockResolvedValueOnce({ ...oldKey, isActive: false, revokedAt: new Date() })
        .mockResolvedValueOnce(newKey)
        .mockResolvedValue({});
      mockEm.create
        .mockReturnValueOnce(newKey)
        .mockReturnValue({} as ApiKeyAuditLog);

      const result = await service.rotate('old-key-id', 'admin');

      expect(result.plaintext_key).toMatch(/^aip_/);
      expect(dataSource.transaction).toHaveBeenCalled();
    });

    it('throws NotFoundException when old key not found', async () => {
      const mockEm = (dataSource as unknown as { _mockEm: { findOne: jest.Mock } })._mockEm;
      mockEm.findOne.mockResolvedValue(null);

      await expect(service.rotate('nonexistent', 'admin')).rejects.toThrow(NotFoundException);
    });

    it('throws BadRequestException when old key is already inactive', async () => {
      const inactiveKey = makeApiKey({ isActive: false });
      const mockEm = (dataSource as unknown as { _mockEm: { findOne: jest.Mock } })._mockEm;
      mockEm.findOne.mockResolvedValue(inactiveKey);

      await expect(service.rotate('key-uuid-1', 'admin')).rejects.toThrow(BadRequestException);
    });
  });
});
