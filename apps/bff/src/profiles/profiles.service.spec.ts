import { NotFoundException, BadRequestException } from '@nestjs/common';
import { Repository, DataSource } from 'typeorm';
import * as yaml from 'js-yaml';
import { ProfilesService } from './profiles.service';
import { AgentProfile } from '../entities/agent-profile.entity';
import { ProfileHistory } from '../entities/profile-history.entity';
import { ProfileSchemaValidator } from './profile-schema.validator';

function makeDate(offsetMs = 0): Date {
  return new Date(Date.now() + offsetMs);
}

function makeProfile(overrides: Partial<AgentProfile> = {}): AgentProfile {
  return {
    id: 'test-id',
    name: 'Test Profile',
    description: 'desc',
    mode: 'chat',
    config: { id: 'test-id', name: 'Test Profile', mode: 'chat', tools: [] },
    isActive: true,
    createdAt: makeDate(),
    updatedAt: makeDate(),
    ...overrides,
  } as AgentProfile;
}

function makeHistory(overrides: Partial<ProfileHistory> = {}): ProfileHistory {
  return {
    id: 'hist-id',
    profileId: 'test-id',
    yamlContent: yaml.dump({ id: 'test-id', name: 'Test Profile', mode: 'chat' }),
    changedBy: 'admin',
    changedAt: makeDate(),
    comment: '생성',
    ...overrides,
  } as ProfileHistory;
}

function makeProfileRepo(overrides: Partial<Record<string, jest.Mock>> = {}): jest.Mocked<Repository<AgentProfile>> {
  return {
    find: jest.fn(),
    findOne: jest.fn(),
    save: jest.fn(),
    create: jest.fn(),
    remove: jest.fn(),
    ...overrides,
  } as unknown as jest.Mocked<Repository<AgentProfile>>;
}

function makeHistoryRepo(overrides: Partial<Record<string, jest.Mock>> = {}): jest.Mocked<Repository<ProfileHistory>> {
  return {
    find: jest.fn(),
    findOne: jest.fn(),
    save: jest.fn(),
    create: jest.fn(),
    ...overrides,
  } as unknown as jest.Mocked<Repository<ProfileHistory>>;
}

function makeSchemaValidator(valid = true): jest.Mocked<ProfileSchemaValidator> {
  return {
    validate: jest.fn().mockReturnValue(valid ? { ok: true } : { ok: false, errors: ['schema error'] }),
    getSchema: jest.fn().mockReturnValue({}),
    onModuleInit: jest.fn(),
  } as unknown as jest.Mocked<ProfileSchemaValidator>;
}

function makeDataSource(): jest.Mocked<DataSource> {
  return {
    query: jest.fn().mockResolvedValue([]),
  } as unknown as jest.Mocked<DataSource>;
}

function makeService(
  profileRepo: jest.Mocked<Repository<AgentProfile>>,
  historyRepo: jest.Mocked<Repository<ProfileHistory>>,
  schemaValidator: jest.Mocked<ProfileSchemaValidator>,
  dataSource: jest.Mocked<DataSource>,
): ProfilesService {
  return new ProfilesService(profileRepo, historyRepo, schemaValidator, dataSource);
}

describe('ProfilesService', () => {
  let profileRepo: jest.Mocked<Repository<AgentProfile>>;
  let historyRepo: jest.Mocked<Repository<ProfileHistory>>;
  let schemaValidator: jest.Mocked<ProfileSchemaValidator>;
  let dataSource: jest.Mocked<DataSource>;
  let service: ProfilesService;

  beforeEach(() => {
    profileRepo = makeProfileRepo();
    historyRepo = makeHistoryRepo();
    schemaValidator = makeSchemaValidator();
    dataSource = makeDataSource();
    service = makeService(profileRepo, historyRepo, schemaValidator, dataSource);
  });

  describe('getSchema', () => {
    it('returns schema from validator', () => {
      const result = service.getSchema();
      expect(schemaValidator.getSchema).toHaveBeenCalled();
      expect(result).toEqual({});
    });
  });

  describe('findAll', () => {
    it('returns empty array when no profiles', async () => {
      profileRepo.find.mockResolvedValue([]);

      const result = await service.findAll();

      expect(result).toEqual([]);
      expect(profileRepo.find).toHaveBeenCalledWith({ order: { createdAt: 'DESC' } });
    });

    it('returns mapped list items when profiles exist', async () => {
      const profile = makeProfile();
      profileRepo.find.mockResolvedValue([profile]);

      const result = await service.findAll();

      expect(result).toHaveLength(1);
      expect(result[0].id).toBe('test-id');
      expect(result[0].name).toBe('Test Profile');
      expect(result[0].mode).toBe('chat');
      expect(result[0].isActive).toBe(true);
    });

    it('maps config fields correctly', async () => {
      const profile = makeProfile({
        config: {
          id: 'p1',
          name: 'P1',
          mode: 'rag',
          tools: [{ name: 'tool1' }, { name: 'tool2' }],
          domain_scopes: ['finance'],
          security_level_max: 'INTERNAL',
          router_model: 'opus',
          main_model: 'sonnet',
        },
      });
      profileRepo.find.mockResolvedValue([profile]);

      const result = await service.findAll();

      expect(result[0].toolsCount).toBe(2);
      expect(result[0].domainScopes).toEqual(['finance']);
      expect(result[0].securityLevelMax).toBe('INTERNAL');
      expect(result[0].routerModel).toBe('opus');
      expect(result[0].mainModel).toBe('sonnet');
    });
  });

  describe('findOne', () => {
    it('returns profile detail when profile exists', async () => {
      const profile = makeProfile();
      profileRepo.findOne.mockResolvedValue(profile);

      const result = await service.findOne('test-id');

      expect(result.id).toBe('test-id');
      expect(result.yamlContent).toBeDefined();
      expect(result.config).toBeDefined();
      expect(result.createdAt).toBeDefined();
    });

    it('throws NotFoundException when profile not found', async () => {
      profileRepo.findOne.mockResolvedValue(null);

      await expect(service.findOne('nonexistent')).rejects.toThrow(NotFoundException);
    });
  });

  describe('create', () => {
    const validYaml = yaml.dump({ id: 'new-profile', name: 'New Profile', mode: 'chat' });

    it('creates profile and records initial history on success', async () => {
      const profile = makeProfile({ id: 'new-profile', name: 'New Profile' });
      profileRepo.create.mockReturnValue(profile);
      profileRepo.save.mockResolvedValue(profile);
      historyRepo.create.mockReturnValue(makeHistory({ comment: '생성' }));
      historyRepo.save.mockResolvedValue(makeHistory({ comment: '생성' }));

      const result = await service.create(validYaml, 'admin');

      expect(profileRepo.create).toHaveBeenCalled();
      expect(profileRepo.save).toHaveBeenCalled();
      expect(historyRepo.save).toHaveBeenCalled();
      expect(result.id).toBe('new-profile');
    });

    it('throws BadRequestException when schema validation fails', async () => {
      schemaValidator.validate.mockReturnValue({ ok: false, errors: ['invalid schema'] });

      await expect(service.create(validYaml, 'admin')).rejects.toThrow(BadRequestException);
      expect(profileRepo.save).not.toHaveBeenCalled();
    });

    it('throws BadRequestException when id field is missing', async () => {
      const yamlWithoutId = yaml.dump({ name: 'No ID', mode: 'chat' });

      await expect(service.create(yamlWithoutId, 'admin')).rejects.toThrow(BadRequestException);
    });

    it('throws BadRequestException when name field is missing', async () => {
      const yamlWithoutName = yaml.dump({ id: 'some-id', mode: 'chat' });

      await expect(service.create(yamlWithoutName, 'admin')).rejects.toThrow(BadRequestException);
    });

    it('throws BadRequestException when mode field is missing', async () => {
      const yamlWithoutMode = yaml.dump({ id: 'some-id', name: 'Some Name' });

      await expect(service.create(yamlWithoutMode, 'admin')).rejects.toThrow(BadRequestException);
    });

    it('throws BadRequestException when yaml is invalid', async () => {
      await expect(service.create(': invalid: yaml: [broken', 'admin')).rejects.toThrow(BadRequestException);
    });
  });

  describe('update', () => {
    it('saves previous yaml to history and updates profile', async () => {
      const profile = makeProfile();
      const updatedYaml = yaml.dump({ id: 'test-id', name: 'Updated Name', mode: 'rag' });
      profileRepo.findOne.mockResolvedValue(profile);
      profileRepo.save.mockResolvedValue({ ...profile, name: 'Updated Name', mode: 'rag' } as AgentProfile);
      historyRepo.create.mockReturnValue(makeHistory({ comment: '수정 전 백업' }));
      historyRepo.save.mockResolvedValue(makeHistory());

      const result = await service.update('test-id', updatedYaml, 'admin');

      expect(historyRepo.save).toHaveBeenCalledWith(
        expect.objectContaining({ comment: '수정 전 백업' }),
      );
      expect(profileRepo.save).toHaveBeenCalled();
      expect(result.name).toBe('Updated Name');
    });

    it('throws NotFoundException when profile not found', async () => {
      profileRepo.findOne.mockResolvedValue(null);

      await expect(service.update('nonexistent', yaml.dump({ id: 'x', name: 'X', mode: 'chat' }), 'admin')).rejects.toThrow(NotFoundException);
    });

    it('throws BadRequestException when schema validation fails on update', async () => {
      profileRepo.findOne.mockResolvedValue(makeProfile());
      historyRepo.create.mockReturnValue(makeHistory());
      historyRepo.save.mockResolvedValue(makeHistory());
      schemaValidator.validate.mockReturnValue({ ok: false, errors: ['bad'] });

      await expect(
        service.update('test-id', yaml.dump({ id: 'test-id', name: 'N', mode: 'chat' }), 'admin'),
      ).rejects.toThrow(BadRequestException);
    });

    it('calls notifyProfileUpdated after successful update', async () => {
      const profile = makeProfile();
      profileRepo.findOne.mockResolvedValue(profile);
      profileRepo.save.mockResolvedValue(profile);
      historyRepo.create.mockReturnValue(makeHistory());
      historyRepo.save.mockResolvedValue(makeHistory());

      await service.update('test-id', yaml.dump({ id: 'test-id', name: 'Test Profile', mode: 'chat' }), 'admin');

      expect(dataSource.query).toHaveBeenCalledWith(
        'SELECT pg_notify($1, $2)',
        ['profile_updated', 'test-id'],
      );
    });
  });

  describe('remove', () => {
    it('removes profile when it exists', async () => {
      const profile = makeProfile();
      profileRepo.findOne.mockResolvedValue(profile);
      profileRepo.remove.mockResolvedValue(profile);

      await service.remove('test-id');

      expect(profileRepo.remove).toHaveBeenCalledWith(profile);
    });

    it('throws NotFoundException when profile not found', async () => {
      profileRepo.findOne.mockResolvedValue(null);

      await expect(service.remove('nonexistent')).rejects.toThrow(NotFoundException);
    });
  });

  describe('activate', () => {
    it('sets isActive to true and saves', async () => {
      const profile = makeProfile({ isActive: false });
      profileRepo.findOne.mockResolvedValue(profile);
      profileRepo.save.mockResolvedValue({ ...profile, isActive: true } as AgentProfile);

      const result = await service.activate('test-id');

      expect(profileRepo.save).toHaveBeenCalledWith(expect.objectContaining({ isActive: true }));
      expect(result.isActive).toBe(true);
    });

    it('throws NotFoundException when profile not found', async () => {
      profileRepo.findOne.mockResolvedValue(null);

      await expect(service.activate('nonexistent')).rejects.toThrow(NotFoundException);
    });
  });

  describe('deactivate', () => {
    it('sets isActive to false and saves', async () => {
      const profile = makeProfile({ isActive: true });
      profileRepo.findOne.mockResolvedValue(profile);
      profileRepo.save.mockResolvedValue({ ...profile, isActive: false } as AgentProfile);

      const result = await service.deactivate('test-id');

      expect(profileRepo.save).toHaveBeenCalledWith(expect.objectContaining({ isActive: false }));
      expect(result.isActive).toBe(false);
    });

    it('throws NotFoundException when profile not found', async () => {
      profileRepo.findOne.mockResolvedValue(null);

      await expect(service.deactivate('nonexistent')).rejects.toThrow(NotFoundException);
    });
  });

  describe('getHistory', () => {
    it('returns empty array when no history', async () => {
      historyRepo.find.mockResolvedValue([]);

      const result = await service.getHistory('test-id');

      expect(result).toEqual([]);
    });

    it('returns mapped history items with correct versions', async () => {
      const hist1 = makeHistory({ id: 'h1', comment: '생성' });
      const hist2 = makeHistory({ id: 'h2', comment: '수정 전 백업' });
      historyRepo.find.mockResolvedValue([hist1, hist2]);

      const result = await service.getHistory('test-id');

      expect(result).toHaveLength(2);
      expect(result[0].version).toBe(2);
      expect(result[1].version).toBe(1);
      expect(result[0].changeType).toBe('create');
      expect(result[1].changeType).toBe('update');
    });

    it('infers restore changeType for restore comment', async () => {
      const hist = makeHistory({ comment: 'restore from version 3' });
      historyRepo.find.mockResolvedValue([hist]);

      const result = await service.getHistory('test-id');

      expect(result[0].changeType).toBe('restore');
    });
  });

  describe('restore', () => {
    it('restores profile from history and saves backup', async () => {
      const historyItem = makeHistory({ id: 'hist-id', yamlContent: yaml.dump({ id: 'test-id', name: 'Old Name', mode: 'chat' }) });
      const profile = makeProfile();
      historyRepo.findOne.mockResolvedValue(historyItem);
      historyRepo.find.mockResolvedValue([historyItem]);
      profileRepo.findOne.mockResolvedValue(profile);
      historyRepo.create.mockReturnValue(makeHistory({ comment: 'restore from version 1' }));
      historyRepo.save.mockResolvedValue(makeHistory());
      profileRepo.save.mockResolvedValue({ ...profile, name: 'Old Name' } as AgentProfile);

      const result = await service.restore('test-id', 'hist-id', 'admin');

      expect(historyRepo.save).toHaveBeenCalled();
      expect(profileRepo.save).toHaveBeenCalled();
      expect(result).toBeDefined();
    });

    it('throws NotFoundException when history item not found', async () => {
      historyRepo.findOne.mockResolvedValue(null);

      await expect(service.restore('test-id', 'nonexistent', 'admin')).rejects.toThrow(NotFoundException);
    });

    it('throws NotFoundException when profile not found', async () => {
      const historyItem = makeHistory();
      historyRepo.findOne.mockResolvedValue(historyItem);
      historyRepo.find.mockResolvedValue([historyItem]);
      profileRepo.findOne.mockResolvedValue(null);

      await expect(service.restore('test-id', 'hist-id', 'admin')).rejects.toThrow(NotFoundException);
    });

    it('throws BadRequestException when restored yaml fails schema validation', async () => {
      const historyItem = makeHistory({ yamlContent: yaml.dump({ id: 'test-id', name: 'Old', mode: 'chat' }) });
      historyRepo.findOne.mockResolvedValue(historyItem);
      historyRepo.find.mockResolvedValue([historyItem]);
      profileRepo.findOne.mockResolvedValue(makeProfile());
      historyRepo.create.mockReturnValue(makeHistory());
      historyRepo.save.mockResolvedValue(makeHistory());
      schemaValidator.validate.mockReturnValue({ ok: false, errors: ['invalid'] });

      await expect(service.restore('test-id', 'hist-id', 'admin')).rejects.toThrow(BadRequestException);
    });
  });

  describe('getTools', () => {
    it('returns static tool list', async () => {
      const tools = await service.getTools();
      expect(tools.length).toBeGreaterThan(0);
      expect(tools[0]).toHaveProperty('name');
      expect(tools[0]).toHaveProperty('description');
    });
  });

  describe('notifyProfileUpdated', () => {
    it('calls dataSource.query with pg_notify', async () => {
      await service.notifyProfileUpdated('profile-id');

      expect(dataSource.query).toHaveBeenCalledWith(
        'SELECT pg_notify($1, $2)',
        ['profile_updated', 'profile-id'],
      );
    });

    it('does not throw when query fails', async () => {
      dataSource.query.mockRejectedValue(new Error('DB error'));

      await expect(service.notifyProfileUpdated('profile-id')).resolves.toBeUndefined();
    });
  });

  describe('getHistoryDiff', () => {
    it('returns diff when history item exists', async () => {
      const currentHist = makeHistory({ id: 'hist-1', yamlContent: yaml.dump({ id: 'p1', name: 'V2', mode: 'chat' }) });
      const prevHist = makeHistory({ id: 'hist-0', yamlContent: yaml.dump({ id: 'p1', name: 'V1', mode: 'chat' }) });
      historyRepo.findOne
        .mockResolvedValueOnce(currentHist)
        .mockResolvedValueOnce(prevHist);

      const result = await service.getHistoryDiff('test-id', 'hist-1');

      expect(result.history_id).toBe('hist-1');
      expect(result.diff).toBeDefined();
    });

    it('throws NotFoundException when history item not found', async () => {
      historyRepo.findOne.mockResolvedValue(null);

      await expect(service.getHistoryDiff('test-id', 'nonexistent')).rejects.toThrow(NotFoundException);
    });
  });

  describe('js-yaml dump/load behavior', () => {
    it('toDetail produces valid yaml from config', async () => {
      const config = { id: 'test-id', name: 'Test', mode: 'chat', tools: [] };
      const profile = makeProfile({ config });
      profileRepo.findOne.mockResolvedValue(profile);

      const detail = await service.findOne('test-id');

      const parsed = yaml.load(detail.yamlContent);
      expect(parsed).toEqual(config);
    });
  });
});
