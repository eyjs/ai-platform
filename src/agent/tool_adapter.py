"""Tool Protocol -> LangChain StructuredTool 변환.

기존 Tool/ScopedTool을 create_react_agent에서 사용할 수 있게 변환한다.
SearchScope는 클로저로 바인딩하여 LLM에 노출하지 않는다.
"""

import inspect
from typing import Any, Union

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, create_model

from src.domain.models import SearchScope
from src.tools.base import AgentContext, ScopedTool, Tool, ToolResult

MAX_TOOL_RESULT_LEN = 2000


def _format_tool_result(result: ToolResult) -> str:
    """ToolResult -> LLM에 반환할 텍스트."""
    if not result.success:
        return f"Error: {result.error}"

    if isinstance(result.data, list):
        parts = []
        for i, item in enumerate(result.data[:10], 1):
            if isinstance(item, dict):
                title = item.get("title", item.get("file_name", ""))
                content = item.get("content", "")
                if "subject" in item and "predicate" in item:
                    content = f"{item['subject']} — {item['predicate']}: {item['object']}"
                parts.append(f"[{i}] {title}\n{content[:300]}")
            else:
                parts.append(f"[{i}] {str(item)[:300]}")
        return "\n\n".join(parts)

    return str(result.data)[:MAX_TOOL_RESULT_LEN]


def _build_args_schema(name: str, input_schema: dict) -> type[BaseModel]:
    """input_schema -> Pydantic BaseModel 동적 생성."""
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))

    fields: dict[str, Any] = {}
    for prop_name, prop_def in properties.items():
        py_type = str  # 기본값
        type_str = prop_def.get("type", "string")
        if type_str == "integer":
            py_type = int
        elif type_str == "number":
            py_type = float
        elif type_str == "boolean":
            py_type = bool

        description = prop_def.get("description", "")

        if prop_name in required:
            fields[prop_name] = (py_type, ...)
        else:
            fields[prop_name] = (py_type, None)

    if not fields:
        fields["query"] = (str, ...)

    return create_model(f"{name}_Args", **fields)


def convert_tools_to_langchain(
    tools: list[Union[Tool, ScopedTool]],
    context: AgentContext,
    scope: SearchScope,
) -> list[StructuredTool]:
    """Tool Protocol 도구들을 LangChain StructuredTool로 변환한다."""
    converted = []

    for tool in tools:
        # Protocol isinstance는 unreliable (양쪽 다 매칭)
        # execute() 시그니처에 'scope' 파라미터가 있으면 ScopedTool
        sig = inspect.signature(tool.execute)
        is_scoped = "scope" in sig.parameters
        args_schema = _build_args_schema(tool.name, tool.input_schema)

        # 클로저로 tool, scope, context 바인딩
        _tool = tool
        _is_scoped = is_scoped

        async def _ainvoke(_t=_tool, _s=_is_scoped, **kwargs) -> str:
            if _s:
                result = await _t.execute(params=kwargs, context=context, scope=scope)
            else:
                result = await _t.execute(params=kwargs, context=context)
            return _format_tool_result(result)

        lc_tool = StructuredTool.from_function(
            coroutine=_ainvoke,
            name=tool.name,
            description=tool.description,
            args_schema=args_schema,
        )
        converted.append(lc_tool)

    return converted
