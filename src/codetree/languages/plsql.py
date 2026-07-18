"""Oracle PL/SQL language plugin backed by njank/tree-sitter-plsql.

Covers packages (spec + body), standalone procedures/functions, object
types (spec + body incl. constructors and member routines), triggers,
cursors and package-level type definitions.

Identifier matching is CASE-INSENSITIVE (unquoted PL/SQL identifiers are
case-insensitive; quoted-identifier semantics are intentionally ignored
in v1). Reference/call results are name-based candidates: overloading,
schema qualification, synonyms and dynamic SQL are not resolved.
"""

import re

from tree_sitter import Language, Parser, Query
import tree_sitter_plsql as tsplsql

from .base import LanguagePlugin, _clean_doc, _matches

_LANGUAGE = Language(tsplsql.language())
_PARSER = Parser(_LANGUAGE)


# One-entry memo caches. The indexer and graph builder call the extraction
# methods once per routine with the SAME source blob (FileEntry.source), so
# re-parsing plus re-running all definition queries on every call made server
# startup quadratic in routines per file (minutes for a large package body).
_TREE_CACHE: dict = {"source": None, "tree": None}
_DEF_INDEX_CACHE: dict = {"tree": None, "index": None}
_CALLS_INDEX_CACHE: dict = {"tree": None, "index": None}


def _parse(source: bytes):
    if _TREE_CACHE["source"] is source:
        return _TREE_CACHE["tree"]
    tree = _PARSER.parse(source)
    _TREE_CACHE["source"] = source
    _TREE_CACHE["tree"] = tree
    return tree


def _text(node) -> str:
    return node.text.decode("utf-8", errors="replace")


def _squash(s: str) -> str:
    """Collapse runs of whitespace (multi-line parameter lists -> one line)."""
    return re.sub(r"\s+", " ", s).strip()


# Node types that act as symbol CONTAINERS. Used to attribute a `parent` to
# nested routines by walking up the tree.
_CONTAINER_TYPES = {
    "create_package": "package_name",
    "create_package_body": "package_name",
    "create_type_body": "type_name",
    "create_procedure": "prc_name",
    "create_function": "fnc_name",
    "procedure_definition": "prc_name",
    "function_definition": "fnc_name",
}

# Definition queries: (query, capture kind). `@name` = identifier, `@def` =
# whole definition node, optional `@params` = parameter list node.
# Node/field names verified against src/node-types.json of the grammar.
_DEF_QUERIES: list[tuple[str, str]] = [
    ("(create_package package_name: (identifier) @name) @def", "class"),
    ("(create_package_body package_name: (identifier) @name) @def", "class"),
    ("(create_type (plsql_type_source type_name: (identifier) @name)) @def", "class"),
    ("(create_type_body type_name: (identifier) @name) @def", "class"),
    ("(create_trigger trigger_name: (identifier) @name) @def", "function"),
    (
        "(create_procedure prc_name: (identifier) @name (parameter_declaration)? @params) @def",
        "function",
    ),
    (
        "(create_function fnc_name: (identifier) @name (parameter_declaration)? @params) @def",
        "function",
    ),
    (
        "(procedure_definition prc_name: (identifier) @name (parameter_declaration)? @params) @def",
        "function",
    ),
    (
        "(function_definition fnc_name: (identifier) @name (parameter_declaration)? @params) @def",
        "function",
    ),
    (
        "(procedure_declaration prc_name: (identifier) @name (parameter_declaration)? @params) @def",
        "function",
    ),
    (
        "(function_declaration fnc_name: (identifier) @name (parameter_declaration)? @params) @def",
        "function",
    ),
    (
        "(element_spec_procedure_spec prc_name: (identifier) @name (parameter_declaration)? @params) @def",
        "function",
    ),
    (
        "(element_spec_function_spec fnc_name: (identifier) @name (parameter_declaration)? @params) @def",
        "function",
    ),
    (
        "(constructor_definition_in_body name: (identifier) @name (parameter_declaration)? @params) @def",
        "function",
    ),
    ("(cursor_definition (identifier) @name) @def", "type"),
    ("(type_definition_record (identifier) @name) @def", "type"),
    ("(type_definition_collection (identifier) @name) @def", "type"),
    ("(type_definition_sub subtype_name: (identifier) @name) @def", "type"),
]

# Compile ONCE, and as a SINGLE multi-pattern query: one cursor traversal
# per tree instead of 18. Separate per-pattern traversals made cold indexing
# of large repos take minutes (QueryCursor.matches dominated the profile).
# Pattern index of a match maps back to its kind via _DEF_KINDS.
_DEF_QUERY = Query(_LANGUAGE, "\n".join(q for q, _kind in _DEF_QUERIES))
_DEF_KINDS: list[str] = [kind for _q, kind in _DEF_QUERIES]
_CALLS_QUERY = Query(
    _LANGUAGE, "(ref_call (referenced_element ref_name: (identifier) @callee))"
)
_LOCALS_QUERY = Query(_LANGUAGE, "(item_declaration (identifier) @name) @decl")
_PARAMS_QUERY = Query(
    _LANGUAGE, "(parameter_declaration_element (identifier) @name) @decl"
)

# Definition-node types that themselves hold executable code, for
# extract_calls_in_function / complexity / variables.
_ROUTINE_TYPES = (
    "procedure_definition",
    "function_definition",
    "create_procedure",
    "create_function",
    "create_trigger",
    "constructor_definition_in_body",
)


def _enclosing_parent(node) -> str | None:
    """Name of the nearest enclosing container ABOVE `node` (or None)."""
    cur = node.parent
    while cur is not None:
        field = _CONTAINER_TYPES.get(cur.type)
        if field:
            name_node = cur.child_by_field_name(field)
            if name_node is None and cur.type == "create_type":
                pass  # create_type name lives in plsql_type_source; not a parent here
            if name_node is not None:
                return _text(name_node)
        elif cur.type == "create_type":
            src = next((c for c in cur.children if c.type == "plsql_type_source"), None)
            if src is not None:
                name_node = src.child_by_field_name("type_name")
                if name_node is not None:
                    return _text(name_node)
        cur = cur.parent
    return None


def _doc_above(def_node) -> str:
    """First meaningful line of the comment block directly above a definition."""
    prev = def_node.prev_named_sibling
    if prev is None or prev.type not in ("comment_sl", "comment_ml"):
        return ""
    first = prev
    while True:
        pp = first.prev_named_sibling
        if (
            pp is not None
            and pp.type in ("comment_sl", "comment_ml")
            and pp.end_point[0] + 1 >= first.start_point[0]
        ):
            first = pp
            continue
        break
    # _clean_doc strips /* * # > decorations but not PL/SQL's `--`
    return _clean_doc(_text(first)).lstrip("- ").strip()


class PlsqlPlugin(LanguagePlugin):
    extensions = (".pks", ".pkb", ".prc", ".fct", ".tps", ".tpb", ".trg")

    def _get_parser(self):
        return _PARSER

    # -- skeleton ---------------------------------------------------------

    def extract_skeleton(self, source: bytes) -> list[dict]:
        tree = _parse(source)
        results = []
        for pattern_idx, m in _matches(_DEF_QUERY, tree.root_node):
            kind = _DEF_KINDS[pattern_idx]
            name_node = m["name"]
            def_node = m["def"]
            parent = _enclosing_parent(def_node)
            sym_type = kind
            if kind == "function" and parent is not None:
                sym_type = "method"
            params = ""
            if "params" in m and m["params"] is not None:
                p = m["params"]
                # guard: the optional capture must belong to THIS def
                if p.start_byte >= def_node.start_byte and p.end_byte <= def_node.end_byte:
                    params = _squash(_text(p))
            results.append(
                {
                    "type": sym_type,
                    "name": _text(name_node),
                    "line": name_node.start_point[0] + 1,
                    "parent": parent,
                    "params": params,
                    "doc": _doc_above(def_node),
                }
            )
        # de-duplicate (name, line) -- e.g. nothing expected, but keep parity
        # with other plugins -- and sort by position.
        seen = set()
        unique = []
        for item in sorted(results, key=lambda x: (x["line"], x["name"].lower())):
            key = (item["name"].lower(), item["line"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    # -- symbol source ----------------------------------------------------

    @staticmethod
    def _def_index(tree) -> dict[str, list]:
        """Lowercased name -> definition nodes, built ONCE per tree.

        Running the definition query costs ~0.4s on a large package
        body; doing that per routine lookup dominated startup time.
        """
        if _DEF_INDEX_CACHE["tree"] is tree:
            return _DEF_INDEX_CACHE["index"]
        index: dict[str, list] = {}
        for _idx, m in _matches(_DEF_QUERY, tree.root_node):
            index.setdefault(_text(m["name"]).lower(), []).append(m["def"])
        _DEF_INDEX_CACHE["tree"] = tree
        _DEF_INDEX_CACHE["index"] = index
        return index

    def _find_def_nodes(self, tree, name: str) -> list:
        """All definition nodes whose declared name matches (case-insensitive).

        Ordered: code-bearing definitions first, then declarations/types.
        """
        hits = list(self._def_index(tree).get(name.lower(), []))
        hits.sort(key=lambda n: (0 if n.type in _ROUTINE_TYPES else 1, n.start_byte))
        return hits

    def extract_symbol_source(self, source: bytes, name: str) -> tuple[str, int] | None:
        tree = _parse(source)
        nodes = self._find_def_nodes(tree, name)
        if not nodes:
            return None
        node = nodes[0]
        text = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        return text, node.start_point[0] + 1

    # -- calls -------------------------------------------------------------

    @staticmethod
    def _calls_index(tree) -> dict[int, set[str]]:
        """Routine def-node id -> callee names, built ONCE per tree.

        One query pass over the whole tree; each call is attributed to ALL
        enclosing routines, matching the old per-routine scoped query which
        also saw calls inside nested routines. Running a separate cursor per
        routine (thousands per package body) dominated the cold-index profile.
        """
        if _CALLS_INDEX_CACHE["tree"] is tree:
            return _CALLS_INDEX_CACHE["index"]
        index: dict[int, set[str]] = {}
        for _idx, m in _matches(_CALLS_QUERY, tree.root_node):
            callee = _text(m["callee"])
            cur = m["callee"].parent
            while cur is not None:
                if cur.type in _ROUTINE_TYPES:
                    index.setdefault(cur.id, set()).add(callee)
                cur = cur.parent
        _CALLS_INDEX_CACHE["tree"] = tree
        _CALLS_INDEX_CACHE["index"] = index
        return index

    def extract_calls_in_function(self, source: bytes, fn_name: str) -> list[str]:
        tree = _parse(source)
        nodes = [n for n in self._find_def_nodes(tree, fn_name) if n.type in _ROUTINE_TYPES]
        if not nodes:
            return []
        calls_index = self._calls_index(tree)
        called: set[str] = set()
        for node in nodes:
            called |= calls_index.get(node.id, set())
        # exclude the routine's own name (recursion still counts as a call,
        # but self-references via END <name> markers do not appear here)
        # secondary key makes ordering deterministic for case-insensitive ties
        return sorted(called, key=lambda s: (s.lower(), s))

    # -- usages -------------------------------------------------------------

    def extract_symbol_usages(self, source: bytes, name: str) -> list[dict]:
        tree = _parse(source)
        escaped = re.escape(name)
        q = Query(_LANGUAGE, f'((identifier) @id (#match? @id "(?i)^{escaped}$"))')
        out = []
        for _, m in _matches(q, tree.root_node):
            n = m["id"]
            out.append({"line": n.start_point[0] + 1, "col": n.start_point[1]})
        out.sort(key=lambda u: (u["line"], u["col"]))
        return out

    # -- imports ------------------------------------------------------------

    def extract_imports(self, source: bytes) -> list[dict]:
        # PL/SQL has no import statements; package references are resolved
        # by the database. Cross-package usage shows up via references.
        return []

    # -- syntax -------------------------------------------------------------

    def check_syntax(self, source: bytes) -> bool:
        return _parse(source).root_node.has_error

    # -- complexity (optional) ----------------------------------------------

    _BRANCH_NODES = {
        "if_statement": "if",
        "kw_elsif": "elsif",
        "basic_loop_statement": "loop",
        "for_loop_statement": "for",
        "while_loop_statement": "while",
        "forall_statement": "forall",
        "case_statement": "case",
        "expression_base_case_search": "case_expr",
        "expression_base_case_simple": "case_expr",
        "exception_handler": "when(exception)",
    }

    def compute_complexity(self, source: bytes, fn_name: str) -> dict | None:
        tree = _parse(source)
        nodes = [n for n in self._find_def_nodes(tree, fn_name) if n.type in _ROUTINE_TYPES]
        if not nodes:
            return None
        breakdown: dict[str, int] = {}

        def walk(n):
            label = self._BRANCH_NODES.get(n.type)
            if label:
                breakdown[label] = breakdown.get(label, 0) + 1
            for c in n.children:
                walk(c)

        for node in nodes:
            walk(node)
        return {"total": 1 + sum(breakdown.values()), "breakdown": breakdown}

    # -- variables (optional) -------------------------------------------------

    def extract_variables(self, source: bytes, fn_name: str) -> list[dict]:
        tree = _parse(source)
        nodes = [n for n in self._find_def_nodes(tree, fn_name) if n.type in _ROUTINE_TYPES]
        if not nodes:
            return []
        out = []
        for node in nodes:
            for query, kind in ((_LOCALS_QUERY, "local"), (_PARAMS_QUERY, "parameter")):
                for _, m in _matches(query, node):
                    decl = m["decl"]
                    # declarations of NESTED routines belong to those routines.
                    # NB: compare node ids -- tree-sitter creates fresh Node
                    # wrappers on every traversal, so `is` never matches.
                    enclosing = _enclosing_routine(decl)
                    if enclosing is None or enclosing.id != node.id:
                        continue
                    name_node = m["name"]
                    type_text = _squash(
                        source[name_node.end_byte : decl.end_byte].decode(
                            "utf-8", errors="replace"
                        )
                    ).rstrip(";,").strip()
                    out.append(
                        {
                            "name": _text(name_node),
                            "line": name_node.start_point[0] + 1,
                            "type": type_text,
                            "kind": kind,
                        }
                    )
        out.sort(key=lambda v: v["line"])
        return out


def _enclosing_routine(node):
    """Nearest ancestor that is a code-bearing routine definition."""
    cur = node.parent
    while cur is not None:
        if cur.type in _ROUTINE_TYPES:
            return cur
        cur = cur.parent
    return None
