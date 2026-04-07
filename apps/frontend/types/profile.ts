export type ProfileMode = 'deterministic' | 'agentic' | 'workflow' | 'hybrid';

export interface ProfileListItem {
  id: string;
  name: string;
  description: string | null;
  mode: ProfileMode;
  domainScopes: string[];
  securityLevelMax: string;
  isActive: boolean;
  toolsCount: number;
  routerModel: string;
  mainModel: string;
}

export interface ProfileDetail extends ProfileListItem {
  yamlContent: string;
  config: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface ProfileHistoryItem {
  id: string;
  profileId: string;
  yamlContent: string;
  changedBy: string;
  changedAt: string;
  comment: string | null;
}

export interface ToolItem {
  name: string;
  description: string;
}
