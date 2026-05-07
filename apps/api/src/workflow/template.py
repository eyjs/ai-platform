"""Workflow Template: {{field}} 대입 엔진.

수집된 데이터를 템플릿 문자열에 대입한다.
engine.py와 action_client.py 양쪽에서 ���용한다.

예: "{{name}}님, 연락처를 알려주세요." -> "홍길동님, 연락처를 알려주세요."
"""

from __future__ import annotations


def render_template(template: str, data: dict) -> str:
    """수집된 데��터를 템플릿에 대입한다.

    {{key}} 패턴을 data[key] 값으로 치환한다.
    data에 없는 키는 원본 그대로 유지한다.
    """
    result = template
    for key, value in data.items():
        result = result.replace("{{" + key + "}}", str(value))
    return result


def render_dict_template(template_dict: dict, data: dict) -> dict:
    """dict의 모든 문자열 값에 대해 템플릿을 렌더링한다.

    중첩 dict는 재귀적으로 처리한다.
    리스트 내 문자열도 처리한다.
    """
    result = {}
    for key, value in template_dict.items():
        if isinstance(value, str):
            result[key] = render_template(value, data)
        elif isinstance(value, dict):
            result[key] = render_dict_template(value, data)
        elif isinstance(value, list):
            result[key] = [
                render_template(item, data) if isinstance(item, str) else item
                for item in value
            ]
        else:
            result[key] = value
    return result
