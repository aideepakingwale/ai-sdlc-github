"""The restricted expression language custom agents use for conditions and prompt pieces."""
import pytest

from app.services import safe_expr as e


def test_it_reads_values_and_compares_them():
    v = {"history": [1, 2, 3, 4], "amount": {"value": 120, "currency": "GBP"}, "name": "  Ann "}
    assert e.evaluate("len(history) > 3 and amount.value >= 100", v) is True
    assert e.evaluate("amount.currency == 'GBP' or amount.value < 5", v) is True
    assert e.evaluate("name.strip().lower()", v) == "ann"
    assert e.evaluate("amount.value * 2 if amount.value > 100 else 0", v) == 240
    assert e.evaluate("2 in history and 9 not in history", v) is True
    assert e.evaluate("missing_field", {"missing_field": None}) is None


@pytest.mark.parametrize("bad", ["__import__('os').system('x')", "a.__class__", "open('f')", "lambda: 1", "[x for x in a]", "f'{a}'", "a; b", "a = 1", "exec('1')", "(yield)", "a.upper().__len__()"])
def test_it_refuses_anything_that_is_not_a_read(bad):
    with pytest.raises((e.ExprError,)):
        e.evaluate(bad, {"a": "x", "b": 1})


def test_it_is_size_limited_and_explains_unknown_names():
    with pytest.raises(e.ExprError, match="longer than"):
        e.evaluate("1+" * 400 + "1", {})
    with pytest.raises(e.ExprError, match="Unknown value 'zzz'"):
        e.evaluate("zzz > 1", {"a": 1})


def test_conditions_default_to_true_and_check_finds_problems_without_running():
    assert e.truthy("", {}) and e.truthy("Always", {}) and not e.truthy("a > 1", {"a": 0})
    assert e.check("a > 1", {"a"}) == []
    assert e.check("b > 1", {"a"}) == ["Unknown value 'b'"]
    assert e.check("a >", {"a"})[0].startswith("Cannot read")
    assert any("not allowed" in p for p in e.check("[x for x in a]", {"a"}))
    assert e.check("__x", set())


def test_templates_fill_variables_and_expressions_and_leave_unknowns_visible():
    out = e.render_template("Refund {{ amount.value * 2 }} for {customer}; {missing} stays; {{ nope }}", {"amount": {"value": 5}, "customer": "Ann"})
    assert out.startswith("Refund 10 for Ann; {missing} stays; [expression error:")
    assert e.variables_in("Use {a} and {{ b + 1 }} and {c}") == ["a", "c"]
    assert e.names_in_expressions("x {{ b + len(c) }} {{ 1 }}") == {"b", "c"}
    assert e.expression_problems("{{ b }} {{ a > }}", {"a"})
