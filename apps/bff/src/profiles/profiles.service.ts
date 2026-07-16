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

/**
 * id/name/description 는 agent_profiles 의 실제 컬럼이고 config JSONB 는 이 셋을 담지 않는다.
 * apps/api 의 profile_store 가 그 계약의 주인이다: 읽을 때 컬럼을 config 에 되꽂아
 * 파싱하고(_load 계열), 쓸 때 _profile_to_dict 가 셋을 빼고 직렬화한다.
 * BFF 도 같은 계약을 지켜야 한다 — 안 그러면 API 가 심은 프로필의 YAML 에 id/name 이
 * 없어 스키마 검증(required)에서 막히고, 편집 자체가 불가능해진다.
 */
const PROMOTED_KEYS = ['id', 'name', 'description'] as const;

@Injectable()
export class ProfilesService {
  /** 컬럼 → config 재주입. profile_store 의 읽기 경로와 같은 모양의 YAML 문서를 만든다. */
  private static toDocument(p: AgentProfile): Record<string, unknown> {
    const config = (p.config || {}) as Record<string, unknown>;
    return {
      id: p.id,
      name: p.name,
      ...(p.description ? { description: p.description } : {}),
      ...config,
    };
  }

  /** config 로 저장하기 전 승격 키 제거. _profile_to_dict 의 쓰기 경로와 대응한다. */
  private static stripPromoted(
    parsed: Record<string, unknown>,
  ): Record<string, unknown> {
    const config = { ...parsed };
    for (const key of PROMOTED_KEYS) delete config[key];
    return config;
  }

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
      config: ProfilesService.stripPromoted(parsed),
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

    const parsed = this.parseYaml(yamlContent);

    const schemaResult = this.schemaValidator.validate(parsed);
    if (!schemaResult.ok) {
      throw new BadRequestException({
        message: 'Profile schema validation failed',
        errors: schemaResult.errors,
      });
    }

    // 백업은 검증을 통과한 뒤에 남긴다 — 앞에 두면 거부된 수정도 '수정 전 백업' 행을
    // 남겨 히스토리가 실제로 일어나지 않은 변경으로 오염된다.
    const previousYaml = yaml.dump(ProfilesService.toDocument(profile), {
      lineWidth: -1,
    });
    await this.historyRepo.save(
      this.historyRepo.create({
        profileId: id,
        yamlContent: previousYaml,
        changedBy,
        comment: '수정 전 백업',
      }),
    );

    profile.name = (parsed.name as string) || profile.name;
    // description 은 빈 문자열도 유효한 값이다. || 로 폴백하면 지울 수가 없다.
    if (typeof parsed.description === 'string') {
      profile.description = parsed.description || null;
    }
    profile.config = ProfilesService.stripPromoted(parsed);

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
    return items.map((h, index) => ({
      id: h.id,
      profileId: h.profileId,
      yamlContent: h.yamlContent,
      changedBy: h.changedBy,
      changedAt: h.changedAt.toISOString(),
      comment: h.comment,
      changeType: this.inferChangeType(h.comment),
      version: items.length - index,
    }));
  }

  private inferChangeType(comment: string | null): 'create' | 'update' | 'restore' {
    if (!comment) return 'update';
    if (comment === '생성') return 'create';
    if (comment.includes('restore') || comment.includes('복원')) return 'restore';
    return 'update';
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

    // Get version number for the history item
    const allHistory = await this.historyRepo.find({
      where: { profileId: id },
      order: { changedAt: 'DESC' },
    });
    const historyIndex = allHistory.findIndex(h => h.id === historyId);
    const version = allHistory.length - historyIndex;

    const profile = await this.profileRepo.findOne({ where: { id } });
    if (!profile) throw new NotFoundException(`Profile ${id} not found`);

    const parsed = this.parseYaml(historyItem.yamlContent);
    const schemaResult = this.schemaValidator.validate(parsed);
    if (!schemaResult.ok) {
      throw new BadRequestException({
        message: 'Profile schema validation failed',
        errors: schemaResult.errors,
      });
    }

    // 복원 대상이 검증을 통과한 뒤에 현재 상태를 백업한다 (update 와 같은 이유).
    const currentYaml = yaml.dump(ProfilesService.toDocument(profile), {
      lineWidth: -1,
    });
    await this.historyRepo.save(
      this.historyRepo.create({
        profileId: id,
        yamlContent: currentYaml,
        changedBy,
        comment: `restore from version ${version}`,
      }),
    );

    profile.name = (parsed.name as string) || profile.name;
    if (typeof parsed.description === 'string') {
      profile.description = parsed.description || null;
    }
    profile.config = ProfilesService.stripPromoted(parsed);

    await this.profileRepo.save(profile);
    await this.notifyProfileUpdated(profile.id);
    return this.toDetail(profile);
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
      mode: (config.mode as string) || '',
      domainScopes: (config.domain_scopes as string[]) || [],
      securityLevelMax: (config.security_level_max as string) || 'PUBLIC',
      isActive: p.isActive,
      toolsCount: tools.length,
      // 빈 문자열 = 서버 기본 DGX 모델. 여기서 특정 모델명을 기본값으로 끼워넣으면
      // 목록이 실제 서빙과 어긋난다 — 모델 이름의 출처는 DGX(/api/tags) 하나뿐이다.
      mainModel: (config.main_model as string) || '',
    };
  }

  private toDetail(p: AgentProfile): ProfileDetailDto {
    const listItem = this.toListItem(p);
    return {
      ...listItem,
      yamlContent: yaml.dump(ProfilesService.toDocument(p), { lineWidth: -1 }),
      config: (p.config || {}) as Record<string, unknown>,
      createdAt: p.createdAt.toISOString(),
      updatedAt: p.updatedAt.toISOString(),
    };
  }
}
