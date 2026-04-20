import {
  Injectable,
  NotFoundException,
  BadRequestException,
} from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { DataSource, Repository } from 'typeorm';
import * as yaml from 'js-yaml';
import { AgentProfile } from '../entities/agent-profile.entity';
import { ProfileHistory } from '../entities/profile-history.entity';
import {
  ProfileListItemDto,
  ProfileDetailDto,
  ProfileHistoryItemDto,
  ToolItemDto,
} from './dto/profile-response.dto';
import { ProfileSchemaValidator } from './profile-schema.validator';
import { computeDiff, DiffResult } from './profile-diff.util';

@Injectable()
export class ProfilesService {
  constructor(
    @InjectRepository(AgentProfile)
    private readonly profileRepo: Repository<AgentProfile>,
    @InjectRepository(ProfileHistory)
    private readonly historyRepo: Repository<ProfileHistory>,
    private readonly schemaValidator: ProfileSchemaValidator,
    private readonly dataSource: DataSource,
  ) {}

  getSchema(): Record<string, unknown> {
    return this.schemaValidator.getSchema();
  }

  async getHistoryDiff(
    profileId: string,
    historyId: string,
  ): Promise<{ history_id: string; previous_history_id: string | null; diff: DiffResult }> {
    const current = await this.historyRepo.findOne({
      where: { id: historyId, profileId },
    });
    if (!current) throw new NotFoundException('history not found');
    const previous = await this.historyRepo.findOne({
      where: { profileId },
      order: { changedAt: 'DESC' },
    });
    const currentCfg = this.safeParseYaml(current.yamlContent);
    const prevCfg =
      previous && previous.id !== current.id
        ? this.safeParseYaml(previous.yamlContent)
        : {};
    return {
      history_id: current.id,
      previous_history_id: previous && previous.id !== current.id ? previous.id : null,
      diff: computeDiff(prevCfg, currentCfg),
    };
  }

  async notifyProfileUpdated(profileId: string): Promise<void> {
    try {
      await this.dataSource.query('SELECT pg_notify($1, $2)', [
        'profile_updated',
        profileId,
      ]);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn('[profiles] NOTIFY failed', err);
    }
  }

  private safeParseYaml(s: string): Record<string, unknown> {
    try {
      const v = yaml.load(s);
      return v && typeof v === 'object' ? (v as Record<string, unknown>) : {};
    } catch {
      return {};
    }
  }

  async findAll(): Promise<ProfileListItemDto[]> {
    const profiles = await this.profileRepo.find({
      order: { createdAt: 'DESC' },
    });
    return profiles.map((p) => this.toListItem(p));
  }

  async findOne(id: string): Promise<ProfileDetailDto> {
    const profile = await this.profileRepo.findOne({ where: { id } });
    if (!profile) throw new NotFoundException(`Profile ${id} not found`);
    return this.toDetail(profile);
  }

  async create(
    yamlContent: string,
    changedBy: string,
  ): Promise<ProfileDetailDto> {
    const parsed = this.parseYaml(yamlContent);
    if (!parsed.id) throw new BadRequestException('YAML에 id 필드가 필요합니다');
    if (!parsed.name) throw new BadRequestException('YAML에 name 필드가 필요합니다');
    if (!parsed.mode) throw new BadRequestException('YAML에 mode 필드가 필요합니다');

    const schemaResult = this.schemaValidator.validate(parsed);
    if (!schemaResult.ok) {
      throw new BadRequestException({
        message: 'Profile schema validation failed',
        errors: schemaResult.errors,
      });
    }

    const profile = this.profileRepo.create({
      id: parsed.id as string,
      name: parsed.name as string,
      description: (parsed.description as string) || null,
      mode: parsed.mode as string,
      config: parsed,
      isActive: true,
    });

    await this.profileRepo.save(profile);

    // 최초 히스토리 기록
    await this.historyRepo.save(
      this.historyRepo.create({
        profileId: profile.id,
        yamlContent,
        changedBy,
        comment: '생성',
      }),
    );

    return this.toDetail(profile);
  }

  async update(
    id: string,
    yamlContent: string,
    changedBy: string,
  ): Promise<ProfileDetailDto> {
    const profile = await this.profileRepo.findOne({ where: { id } });
    if (!profile) throw new NotFoundException(`Profile ${id} not found`);

    // 이전 상태를 히스토리에 저장
    const previousYaml = yaml.dump(profile.config);
    await this.historyRepo.save(
      this.historyRepo.create({
        profileId: id,
        yamlContent: previousYaml,
        changedBy,
        comment: '수정 전 백업',
      }),
    );

    const parsed = this.parseYaml(yamlContent);

    const schemaResult = this.schemaValidator.validate(parsed);
    if (!schemaResult.ok) {
      throw new BadRequestException({
        message: 'Profile schema validation failed',
        errors: schemaResult.errors,
      });
    }

    profile.name = (parsed.name as string) || profile.name;
    profile.description = (parsed.description as string) || profile.description;
    profile.mode = (parsed.mode as string) || profile.mode;
    profile.config = parsed;

    await this.profileRepo.save(profile);
    await this.notifyProfileUpdated(profile.id);
    return this.toDetail(profile);
  }

  async remove(id: string): Promise<void> {
    const profile = await this.profileRepo.findOne({ where: { id } });
    if (!profile) throw new NotFoundException(`Profile ${id} not found`);
    await this.profileRepo.remove(profile);
  }

  async activate(id: string): Promise<ProfileDetailDto> {
    const profile = await this.profileRepo.findOne({ where: { id } });
    if (!profile) throw new NotFoundException(`Profile ${id} not found`);
    profile.isActive = true;
    await this.profileRepo.save(profile);
    return this.toDetail(profile);
  }

  async deactivate(id: string): Promise<ProfileDetailDto> {
    const profile = await this.profileRepo.findOne({ where: { id } });
    if (!profile) throw new NotFoundException(`Profile ${id} not found`);
    profile.isActive = false;
    await this.profileRepo.save(profile);
    return this.toDetail(profile);
  }

  async getHistory(id: string): Promise<ProfileHistoryItemDto[]> {
    const items = await this.historyRepo.find({
      where: { profileId: id },
      order: { changedAt: 'DESC' },
      take: 50,
    });
    return items.map((h) => ({
      id: h.id,
      profileId: h.profileId,
      yamlContent: h.yamlContent,
      changedBy: h.changedBy,
      changedAt: h.changedAt.toISOString(),
      comment: h.comment,
    }));
  }

  async restore(
    id: string,
    historyId: string,
    changedBy: string,
  ): Promise<ProfileDetailDto> {
    const historyItem = await this.historyRepo.findOne({
      where: { id: historyId, profileId: id },
    });
    if (!historyItem) throw new NotFoundException('히스토리 항목을 찾을 수 없습니다');
    return this.update(id, historyItem.yamlContent, changedBy);
  }

  async getTools(): Promise<ToolItemDto[]> {
    return [
      { name: 'rag_search', description: 'RAG 기반 문서 검색' },
      { name: 'fact_lookup', description: '구조화된 팩트 조회' },
    ];
  }

  private parseYaml(yamlContent: string): Record<string, unknown> {
    try {
      const parsed = yaml.load(yamlContent);
      if (!parsed || typeof parsed !== 'object') {
        throw new BadRequestException('유효하지 않은 YAML입니다');
      }
      return parsed as Record<string, unknown>;
    } catch (error) {
      if (error instanceof BadRequestException) throw error;
      throw new BadRequestException(
        `YAML 파싱 실패: ${error instanceof Error ? error.message : 'unknown'}`,
      );
    }
  }

  private toListItem(p: AgentProfile): ProfileListItemDto {
    const config = (p.config || {}) as Record<string, unknown>;
    const tools = (config.tools as Array<Record<string, unknown>>) || [];
    return {
      id: p.id,
      name: p.name,
      description: p.description,
      mode: p.mode,
      domainScopes: (config.domain_scopes as string[]) || [],
      securityLevelMax: (config.security_level_max as string) || 'PUBLIC',
      isActive: p.isActive,
      toolsCount: tools.length,
      routerModel: (config.router_model as string) || 'sonnet',
      mainModel: (config.main_model as string) || 'sonnet',
    };
  }

  private toDetail(p: AgentProfile): ProfileDetailDto {
    const listItem = this.toListItem(p);
    return {
      ...listItem,
      yamlContent: yaml.dump(p.config, { lineWidth: -1 }),
      config: (p.config || {}) as Record<string, unknown>,
      createdAt: p.createdAt.toISOString(),
      updatedAt: p.updatedAt.toISOString(),
    };
  }
}
