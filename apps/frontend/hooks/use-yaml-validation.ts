'use client';

import { useState, useCallback, useRef, useEffect } from 'react';

export interface ValidationIssue {
  line: number;
  message: string;
  severity: 'error' | 'warning';
}

const REQUIRED_FIELDS = ['id', 'name', 'mode'];
const VALID_MODES = ['deterministic', 'agentic', 'workflow', 'hybrid'];
const VALID_SECURITY_LEVELS = ['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'SECRET'];
const VALID_RESPONSE_POLICIES = ['strict', 'balanced'];
const VALID_MODELS = ['haiku', 'sonnet', 'opus'];
const VALID_MEMORY_TYPES = ['short', 'session', 'long'];

export function useYamlValidation(debounceMs = 300) {
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [isValid, setIsValid] = useState(true);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const validate = useCallback((yamlContent: string) => {
    if (timerRef.current) clearTimeout(timerRef.current);

    timerRef.current = setTimeout(() => {
      const newIssues: ValidationIssue[] = [];

      try {
        // 간단한 YAML 파싱 (라인 기반)
        const lines = yamlContent.split('\n');
        const fields: Record<string, { value: string; line: number }> = {};

        for (let i = 0; i < lines.length; i++) {
          const line = lines[i];
          const match = line.match(/^(\w[\w_]*)\s*:\s*(.*)$/);
          if (match) {
            fields[match[1]] = { value: match[2].trim(), line: i + 1 };
          }
        }

        // 필수 필드 검증
        for (const field of REQUIRED_FIELDS) {
          if (!fields[field] || !fields[field].value) {
            newIssues.push({
              line: fields[field]?.line || 1,
              message: `필수 필드 '${field}'가 비어있습니다`,
              severity: 'error',
            });
          }
        }

        // id 형식 검증
        if (fields.id?.value && !/^[a-z][a-z0-9-]*$/.test(fields.id.value)) {
          newIssues.push({
            line: fields.id.line,
            message: 'id는 영문 소문자와 하이픈만 허용됩니다',
            severity: 'error',
          });
        }

        // mode enum 검증
        if (fields.mode?.value && !VALID_MODES.includes(fields.mode.value)) {
          newIssues.push({
            line: fields.mode.line,
            message: `mode는 ${VALID_MODES.join(', ')} 중 하나여야 합니다`,
            severity: 'error',
          });
        }

        // security_level_max enum 검증
        if (fields.security_level_max?.value && !VALID_SECURITY_LEVELS.includes(fields.security_level_max.value)) {
          newIssues.push({
            line: fields.security_level_max.line,
            message: `security_level_max는 ${VALID_SECURITY_LEVELS.join(', ')} 중 하나여야 합니다`,
            severity: 'error',
          });
        }

        // response_policy 검증
        if (fields.response_policy?.value && !VALID_RESPONSE_POLICIES.includes(fields.response_policy.value)) {
          newIssues.push({
            line: fields.response_policy.line,
            message: `response_policy는 ${VALID_RESPONSE_POLICIES.join(', ')} 중 하나여야 합니다`,
            severity: 'error',
          });
        }

        // model 검증
        for (const model of ['router_model', 'main_model']) {
          if (fields[model]?.value && !VALID_MODELS.includes(fields[model].value)) {
            newIssues.push({
              line: fields[model].line,
              message: `${model}은 ${VALID_MODELS.join(', ')} 중 하나여야 합니다`,
              severity: 'error',
            });
          }
        }

        // memory_type 검증
        if (fields.memory_type?.value && !VALID_MEMORY_TYPES.includes(fields.memory_type.value)) {
          newIssues.push({
            line: fields.memory_type.line,
            message: `memory_type은 ${VALID_MEMORY_TYPES.join(', ')} 중 하나여야 합니다`,
            severity: 'error',
          });
        }

        // 교차 검증: mode=workflow -> workflow_id 필수
        if (fields.mode?.value === 'workflow' && !fields.workflow_id?.value) {
          newIssues.push({
            line: fields.mode.line,
            message: 'mode가 workflow이면 workflow_id가 필수입니다',
            severity: 'error',
          });
        }

        // 경고
        if (!fields.system_prompt?.value) {
          newIssues.push({
            line: 1,
            message: 'system_prompt가 비어있으면 기본 프롬프트가 사용됩니다',
            severity: 'warning',
          });
        }

        if (!fields.tools) {
          newIssues.push({
            line: 1,
            message: 'tools가 비어있으면 RAG 없이 동작합니다',
            severity: 'warning',
          });
        }
      } catch {
        newIssues.push({
          line: 1,
          message: 'YAML 파싱 오류',
          severity: 'error',
        });
      }

      setIssues(newIssues);
      setIsValid(newIssues.filter((i) => i.severity === 'error').length === 0);
    }, debounceMs);
  }, [debounceMs]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  return { issues, isValid, validate };
}
