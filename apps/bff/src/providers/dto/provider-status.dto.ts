/**
 * Provider 상태 응답 DTO
 */
export class ProvidersStatusDto {
  totalProviders: number;
  activeProviders: number;
  cacheEntries: number;
  expiredEntries: number;
  providersByType: ProviderTypeStatusDto[];
  providerMetrics: ProviderMetricsDto[];
}

/**
 * Provider 타입별 상태 DTO
 */
export class ProviderTypeStatusDto {
  providerType: string;
  totalProviders: number;
  activeEntries: number;
  expiredEntries: number;
}

/**
 * 개별 Provider 메트릭 DTO
 */
export class ProviderMetricsDto {
  providerId: string;
  providerType: string;
  cacheEntries: number;
  expiredEntries: number;
  lastActivity: string | null;
  isActive: boolean;
}