export class ProfileListItemDto {
  id: string;
  name: string;
  description: string | null;
  mode: string;
  domainScopes: string[];
  securityLevelMax: string;
  isActive: boolean;
  toolsCount: number;
  routerModel: string;
  mainModel: string;
}

export class ProfileDetailDto extends ProfileListItemDto {
  yamlContent: string;
  config: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export class ProfileHistoryItemDto {
  id: string;
  profileId: string;
  yamlContent: string;
  changedBy: string;
  changedAt: string;
  comment: string | null;
  changeType: 'create' | 'update' | 'restore';
  version: number;
}

export class ToolItemDto {
  name: string;
  description: string;
}
