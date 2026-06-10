"""gateway/routes 모듈의 이름 바인딩 회귀 테스트.

배경: Step 22 god-file 분할 시 chat.py에서 HTTPException import가 누락되어,
인가 거부(403)가 핸들러의 `except HTTPException` 평가 시점에 NameError → 500으로
바뀌는 결함이 라이브에서 발견됨 (2026-06-10). 소스에서 쓰는 이름이 모듈에
실제로 바인딩되어 있는지 정적으로 검증해 동일 부류 회귀를 차단한다.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import inspect
import pathlib

import pytest

_ROUTES_PKG = "src.gateway.routes"
_ROUTES_DIR = pathlib.Path(__file__).parent.parent / "src" / "gateway" / "routes"
_MODULES = sorted(
    p.stem for p in _ROUTES_DIR.glob("*.py") if not p.stem.startswith("__")
)


def _used_names(tree: ast.AST) -> set[str]:
    """모듈 본문에서 Load 컨텍스트로 참조되는 최상위 이름 집합."""
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


@pytest.mark.parametrize("module_name", _MODULES)
def test_route_module_names_are_bound(module_name: str):
    """라우트 모듈이 참조하는 예외/심볼이 모듈 네임스페이스에 존재해야 한다.

    except 절은 예외가 실제 발생하기 전까지 평가되지 않아 import 누락이
    런타임(에러 경로)에서야 NameError로 드러난다. 여기서 선제 검증한다.
    """
    module = importlib.import_module(f"{_ROUTES_PKG}.{module_name}")
    tree = ast.parse(inspect.getsource(module))

    # except 핸들러 타입으로 쓰인 이름은 반드시 바인딩 확인 (이번 결함의 직접 원인)
    handler_names = {
        node.type.id
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
    }
    missing = {name for name in handler_names if not hasattr(module, name) and not hasattr(builtins, name)}
    assert not missing, (
        f"{_ROUTES_PKG}.{module_name}: except 절에서 쓰는 이름이 import되지 않음: {missing}"
    )

    # raise 대상 이름도 동일 검증 (raise HTTPException(...) 패턴)
    raise_names = {
        node.exc.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
    }
    missing_raise = {name for name in raise_names if not hasattr(module, name) and not hasattr(builtins, name)}
    assert not missing_raise, (
        f"{_ROUTES_PKG}.{module_name}: raise에서 쓰는 이름이 import되지 않음: {missing_raise}"
    )
