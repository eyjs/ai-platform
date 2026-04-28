/** Feedback 타입 정의 — contract feedback-dto.md */

export type FeedbackScore = 1 | -1;

export interface SubmitFeedbackBody {
  response_id: string;
  score: FeedbackScore;
  comment?: string;
}

export interface SubmitFeedbackResponse {
  id: string;
  response_id: string;
  score: number;
  created_at: string;
  upserted?: boolean;
}

export interface AdminFeedbackItem {
  id: string;
  response_id: string;
  score: number;
  comment: string | null;
  created_at: string;
  user_id: string;
  profile_id: string | null;
  faithfulness_score: number | null;
  question_preview: string | null;
  answer_preview: string | null;
  response_ts: string | null;
  routing_info: string | null;
  tools_used: string[] | null;
}

export interface AdminFeedbackPage {
  items: AdminFeedbackItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminFeedbackListQuery {
  limit?: number;
  offset?: number;
  only_negative?: boolean;
  date_from?: string;
  date_to?: string;
  profile_id?: string;
  keyword?: string;
}
