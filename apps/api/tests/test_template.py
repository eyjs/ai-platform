"""Workflow Template 렌더링 테스트.

render_template, render_dict_template 함수를 검증한다.
"""

from src.workflow.template import render_dict_template, render_template


class TestRenderTemplate:

    def test_simple_substitution(self):
        assert render_template("{{name}}님 안녕", {"name": "홍길동"}) == "홍길동님 안녕"

    def test_multiple_keys(self):
        result = render_template("{{a}} + {{b}} = {{c}}", {"a": "1", "b": "2", "c": "3"})
        assert result == "1 + 2 = 3"

    def test_missing_key_preserved(self):
        assert render_template("{{name}}님", {}) == "{{name}}님"

    def test_partial_keys(self):
        result = render_template("{{a}} and {{b}}", {"a": "X"})
        assert result == "X and {{b}}"

    def test_non_string_value(self):
        result = render_template("숫자: {{num}}", {"num": 42})
        assert result == "숫자: 42"

    def test_empty_template(self):
        assert render_template("", {"key": "val"}) == ""

    def test_empty_data(self):
        assert render_template("no vars", {}) == "no vars"

    def test_repeated_key(self):
        result = render_template("{{x}} and {{x}}", {"x": "Y"})
        assert result == "Y and Y"


class TestRenderDictTemplate:

    def test_simple_dict(self):
        result = render_dict_template(
            {"greeting": "Hello {{name}}", "static": "unchanged"},
            {"name": "World"},
        )
        assert result == {"greeting": "Hello World", "static": "unchanged"}

    def test_nested_dict(self):
        result = render_dict_template(
            {"outer": {"inner": "{{val}}"}},
            {"val": "deep"},
        )
        assert result == {"outer": {"inner": "deep"}}

    def test_list_values(self):
        result = render_dict_template(
            {"items": ["{{a}}", "static", "{{b}}"]},
            {"a": "X", "b": "Y"},
        )
        assert result == {"items": ["X", "static", "Y"]}

    def test_non_string_values_preserved(self):
        result = render_dict_template(
            {"count": 42, "flag": True, "name": "{{user}}"},
            {"user": "test"},
        )
        assert result == {"count": 42, "flag": True, "name": "test"}

    def test_list_with_non_string_items(self):
        result = render_dict_template(
            {"mixed": ["{{a}}", 123, None]},
            {"a": "text"},
        )
        assert result == {"mixed": ["text", 123, None]}

    def test_empty_dict(self):
        assert render_dict_template({}, {"key": "val"}) == {}
