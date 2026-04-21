import {
  HttpException,
  HttpStatus,
  Injectable,
  Logger,
} from '@nestjs/common';
import { InjectDataSource } from '@nestjs/typeorm';
import { DataSource } from 'typeorm';
import { SubmitFeedbackDto } from './dto/submit-feedback.dto';
import type {
  AdminFeedbackItem,
  AdminFeedbackPage,
  ListFeedbackDto,
} from './dto/list-feedback.dto';

/**
 * FeedbackService
 * - submit: api /api/feedback 로 중계 (JWT 포워딩)
 * - list  : PostgreSQL 직접 읽기 (BFF 컨벤션)
 *
 * 왜 submit 만 api 로 중계하는가?
 * - 요구사항 "프론트엔드 피드백 요청을 api로 중계" 를 명시 준수
 * - user_id 추출/인증 체인을 api 측에서 일관되게 처리 (api 가 단일 auth authority)
 * 왜 list 는 DB 직접인가?
 * - admin 쿼리로, 단순 SELECT. HTTP 왕복 감소. BFF 원칙(DB 직접) 부합.
 */
@Injectable()
export class FeedbackService {
  private readonly logger = new Logger(FeedbackService.name);
  private readonly apiUrl: string;

  constructor(
    @InjectDataSource()
    private readonly dataSource: DataSource,
  ) {
    this.apiUrl = process.env.AIP_API_URL || 'http://localhost:8000';
  }

  async submit(
    dto: SubmitFeedbackDto,
    authorization: string | undefined,
  ): Promise<unknown> {
    if (!authorization) {
      throw new HttpException(
        { success: false, error: { code: 'UNAUTHORIZED', message: '인증이 필요합니다.' } },
        HttpStatus.UNAUTHORIZED,
      );
    }

    let res: Response;
    try {
      res = await fetch(`${this.apiUrl}/api/feedback`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: authorization,
        },
        body: JSON.stringify(dto),
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.logger.error(`feedback.submit.network_error ${message}`);
      throw new HttpException(
        { success: false, error: { code: 'API_UNREACHABLE', message: 'api 호출 실패' } },
        HttpStatus.BAD_GATEWAY,
      );
    }

    const text = await res.text();
    let payload: unknown = undefined;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = text;
      }
    }

    if (!res.ok) {
      this.logger.warn(
        `feedback.submit.api_error status=${res.status} body=${text.slice(0, 200)}`,
      );
      throw new HttpException(
        payload ?? {
          success: false,
          error: { code: 'API_ERROR', message: 'api error' },
        },
        res.status,
      );
    }

    return payload ?? {};
  }

  async list(query: ListFeedbackDto): Promise<AdminFeedbackPage> {
    const limit = Math.min(Math.max(query.limit ?? 50, 1), 200);
    const offset = Math.max(query.offset ?? 0, 0);
    const onlyNegative = Boolean(query.only_negative);
    const dateFrom = query.date_from ?? null;
    const dateTo = query.date_to ?? null;

    // 동적 WHERE — 파라미터는 모두 바인딩 (SQL 인젝션 방지)
    const whereParts: string[] = [];
    const params: unknown[] = [];
    let idx = 1;
    if (onlyNegative) {
      whereParts.push(`f.score = -1`);
    }
    if (dateFrom) {
      whereParts.push(`f.created_at >= $${idx++}`);
      params.push(dateFrom);
    }
    if (dateTo) {
      whereParts.push(`f.created_at < $${idx++}`);
      params.push(dateTo);
    }
    const whereClause = whereParts.length
      ? `WHERE ${whereParts.join(' AND ')}`
      : '';

    const listSql = `
      SELECT
        f.id,
        f.response_id,
        f.score,
        f.comment,
        f.created_at,
        f.user_id,
        l.profile_id,
        l.faithfulness_score,
        l.request_preview  AS question_preview,
        l.response_preview AS answer_preview,
        l.ts               AS response_ts
      FROM response_feedback f
      LEFT JOIN api_request_logs l ON l.response_id = f.response_id
      ${whereClause}
      ORDER BY f.created_at DESC
      LIMIT $${idx++} OFFSET $${idx++}
    `;
    const countSql = `
      SELECT COUNT(*)::int AS c
      FROM response_feedback f
      ${whereClause}
    `;

    try {
      const rows = (await this.dataSource.query(listSql, [
        ...params,
        limit,
        offset,
      ])) as Array<Record<string, unknown>>;
      const countRows = (await this.dataSource.query(
        countSql,
        params,
      )) as Array<{ c: number }>;

      const items: AdminFeedbackItem[] = rows.map((r) => ({
        id: String(r.id),
        response_id: String(r.response_id),
        score: Number(r.score),
        comment: r.comment == null ? null : String(r.comment),
        created_at: new Date(r.created_at as string | Date).toISOString(),
        user_id: String(r.user_id),
        profile_id: r.profile_id == null ? null : String(r.profile_id),
        faithfulness_score:
          r.faithfulness_score == null ? null : Number(r.faithfulness_score),
        question_preview:
          r.question_preview == null ? null : String(r.question_preview),
        answer_preview:
          r.answer_preview == null ? null : String(r.answer_preview),
        response_ts:
          r.response_ts == null
            ? null
            : new Date(r.response_ts as string | Date).toISOString(),
      }));

      return {
        items,
        total: Number(countRows[0]?.c ?? 0),
        limit,
        offset,
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.logger.error(`feedback.list.db_error ${message}`);
      throw new HttpException(
        { success: false, error: { code: 'DB_ERROR', message: '피드백 조회 실패' } },
        HttpStatus.INTERNAL_SERVER_ERROR,
      );
    }
  }
}
