import type { DiffResult } from '../profile-diff.util';

export interface ProfileDiffResponseDto {
  history_id: string;
  previous_history_id: string | null;
  diff: DiffResult;
}
