"""Tests for the Oracle PL/SQL language plugin."""

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_plsql")

from codetree.languages.plsql import PlsqlPlugin  # noqa: E402

FIXTURES = Path(__file__).parent.parent / "fixtures" / "plsql"

plugin = PlsqlPlugin()


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# -- skeleton ---------------------------------------------------------------


def test_skeleton_spec_symbols():
    sk = plugin.extract_skeleton(_read("customer_pkg.pks"))
    by_name = {}
    for item in sk:
        by_name.setdefault(item["name"].lower(), []).append(item)

    assert "customer_pkg" in by_name
    assert by_name["customer_pkg"][0]["type"] == "class"
    assert by_name["customer_pkg"][0]["parent"] is None

    # members carry the package as parent
    assert by_name["getcustomer"][0]["parent"].lower() == "customer_pkg"
    assert by_name["getcustomer"][0]["type"] == "method"
    # overloads: two declarations
    assert len(by_name["getcustomer"]) == 2

    assert "curactive" in by_name
    assert by_name["curactive"][0]["type"] == "type"
    assert "treccustomer" in by_name
    assert "ttabcustomers" in by_name


def test_skeleton_body_nested_routine():
    sk = plugin.extract_skeleton(_read("customer_pkg.pkb"))
    validate = [i for i in sk if i["name"].lower() == "validate"]
    assert len(validate) == 1
    # nested routine's parent is the ENCLOSING routine, not the package
    assert validate[0]["parent"].lower() == "savecustomer"


def test_skeleton_params_and_doc():
    sk = plugin.extract_skeleton(_read("customer_pkg.pks"))
    get1 = [i for i in sk if i["name"].lower() == "getcustomer"][0]
    assert "pnid" in get1["params"].lower()
    assert get1["doc"].startswith("Returns the customer record")


def test_skeleton_lines_are_one_based():
    sk = plugin.extract_skeleton(_read("customer_pkg.pks"))
    pkg = [i for i in sk if i["name"].lower() == "customer_pkg"][0]
    assert pkg["line"] == 1


# -- symbol source ------------------------------------------------------------


def test_symbol_source_function():
    src, line = plugin.extract_symbol_source(_read("customer_pkg.pkb"), "saveCustomer")
    assert src.lower().startswith("procedure savecustomer")
    assert "end saveCustomer" in src


def test_symbol_source_case_insensitive():
    assert plugin.extract_symbol_source(_read("customer_pkg.pkb"), "SAVECUSTOMER") is not None


def test_symbol_source_prefers_definition_over_declaration():
    src, _ = plugin.extract_symbol_source(_read("customer_pkg.pkb"), "getCustomer")
    assert "begin" in src.lower()


def test_symbol_source_missing():
    assert plugin.extract_symbol_source(_read("customer_pkg.pkb"), "nosuch") is None


# -- calls ---------------------------------------------------------------------


def test_calls_in_function():
    calls = plugin.extract_calls_in_function(_read("customer_pkg.pkb"), "getCustomer")
    lower = {c.lower() for c in calls}
    assert "logaccess" in lower
    assert "upper" in lower


def test_calls_not_from_comments_or_strings():
    calls = plugin.extract_calls_in_function(_read("customer_pkg.pkb"), "saveCustomer")
    lower = {c.lower() for c in calls}
    assert "validate" in lower


def test_calls_unknown_function():
    assert plugin.extract_calls_in_function(_read("customer_pkg.pkb"), "nosuch") == []


# -- usages ----------------------------------------------------------------------


def test_usages_exclude_comments_and_strings():
    source = _read("customer_pkg.pkb")
    usages = plugin.extract_symbol_usages(source, "getCustomer")
    lines = [u["line"] for u in usages]
    text = source.decode()
    for u in usages:
        line_text = text.splitlines()[u["line"] - 1]
        # occurrence is a real identifier: starts exactly at col
        assert line_text[u["col"] : u["col"] + len("getCustomer")].lower() == "getcustomer"
    # line 8 comment / line 9 string literal must NOT be reported
    comment_line = next(
        i + 1 for i, l in enumerate(text.splitlines()) if "in this comment" in l
    )
    assert comment_line not in lines


def test_usages_case_insensitive():
    usages = plugin.extract_symbol_usages(_read("customer_pkg.pkb"), "GETCUSTOMER")
    assert usages  # finds getCustomer occurrences regardless of case


# -- misc ------------------------------------------------------------------------


def test_imports_empty():
    assert plugin.extract_imports(_read("customer_pkg.pkb")) == []


def test_check_syntax_ok():
    assert plugin.check_syntax(_read("customer_pkg.pkb")) is False
    assert plugin.check_syntax(_read("customer_pkg.pks")) is False


def test_check_syntax_broken():
    assert plugin.check_syntax(b"create or replace package p is\n  function ( broken") is True


def test_empty_file():
    assert plugin.extract_skeleton(b"") == []


def test_complexity():
    c = plugin.compute_complexity(_read("customer_pkg.pkb"), "saveCustomer")
    assert c is not None
    assert c["total"] >= 3  # if + for-loop + exception handler
    assert c["breakdown"].get("if") == 1
    assert c["breakdown"].get("for") == 1


def test_variables():
    vs = plugin.extract_variables(_read("customer_pkg.pkb"), "getCustomer")
    names = {v["name"].lower() for v in vs}
    assert "vreccustomer" in names
    assert "pnsid" in names or "psname" in names  # parameters included
    # nested routine's locals stay out of the outer routine
    vs_save = plugin.extract_variables(_read("customer_pkg.pkb"), "saveCustomer")
    save_names = {v["name"].lower() for v in vs_save}
    assert "vbok" not in save_names


# -- registry ----------------------------------------------------------------------


def test_registry_registration():
    from codetree.registry import PLUGINS

    for ext in (".pks", ".pkb", ".prc", ".fct", ".tps", ".tpb", ".trg"):
        assert ext in PLUGINS, f"{ext} not registered"
    assert ".sql" not in PLUGINS or not isinstance(PLUGINS.get(".sql"), PlsqlPlugin)
