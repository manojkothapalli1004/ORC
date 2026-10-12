"""Guards against silent function shadowing in backend/api modules.

A second module-level `_job_payload` (scheduler) once silently replaced the
builder-job one in routes.py and broke builder-job dispatch. These tests
AST-scan every backend/api/*.py module so that cannot happen again, and also
ensure route-handler function names are unique across API modules.
"""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_API_DIR = _REPO_ROOT / "backend" / "api"

_ROUTE_DECORATOR_ATTRS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "trace", "api_route", "websocket"}
)


def _top_level_functions(tree: ast.Module) -> list:
    return [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def find_duplicate_defs(source: str) -> list:
    """Return sorted names of top-level functions defined more than once in `source`."""
    tree = ast.parse(source)
    names = [n.name for n in _top_level_functions(tree)]
    return sorted({n for n in names if names.count(n) > 1})


def _is_route_decorator(dec: ast.expr) -> bool:
    target = dec.func if isinstance(dec, ast.Call) else dec
    return isinstance(target, ast.Attribute) and target.attr in _ROUTE_DECORATOR_ATTRS


def route_handler_names(source: str) -> list:
    """Return names of top-level functions decorated as route handlers (e.g. @router.get(...))."""
    tree = ast.parse(source)
    return [
        fn.name
        for fn in _top_level_functions(tree)
        if any(_is_route_decorator(d) for d in fn.decorator_list)
    ]


def find_cross_module_handler_dupes(sources: dict) -> dict:
    """Map handler name -> sorted module names, for handler names used in more than one module."""
    seen: dict = {}
    for module, source in sources.items():
        for name in set(route_handler_names(source)):
            seen.setdefault(name, set()).add(module)
    return {name: sorted(mods) for name, mods in sorted(seen.items()) if len(mods) > 1}


def _api_sources() -> dict:
    return {p.name: p.read_text() for p in sorted(_API_DIR.glob("*.py"))}


def test_routes_module_defines_each_function_once():
    # a second module-level `_job_payload` (scheduler) silently replaced the
    # builder-job one and broke builder-job dispatch
    tree = ast.parse(_REPO_ROOT.joinpath("backend/api/routes.py").read_text())
    names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"functions defined more than once in routes.py: {dupes}"


def test_api_dir_scan_covers_routes_module():
    sources = _api_sources()
    assert "routes.py" in sources


def test_every_api_module_defines_each_function_once():
    offenders = {}
    for module, source in _api_sources().items():
        dupes = find_duplicate_defs(source)
        if dupes:
            offenders[module] = dupes
    assert not offenders, f"functions defined more than once in backend/api modules: {offenders}"


def test_route_handler_names_unique_across_api_modules():
    dupes = find_cross_module_handler_dupes(_api_sources())
    assert not dupes, f"route handler names reused across backend/api modules: {dupes}"


# --- negative / helper tests -------------------------------------------------


def test_find_duplicate_defs_reports_duplicated_def():
    source = (
        "def _job_payload(job):\n"
        "    return {'builder': job}\n"
        "\n"
        "def other():\n"
        "    return 1\n"
        "\n"
        "async def _job_payload(job):\n"
        "    return {'scheduler': job}\n"
    )
    assert find_duplicate_defs(source) == ["_job_payload"]


def test_find_duplicate_defs_ignores_nested_and_unique_defs():
    source = (
        "def a():\n"
        "    def inner():\n"
        "        pass\n"
        "    return inner\n"
        "\n"
        "def b():\n"
        "    def inner():\n"
        "        pass\n"
        "    return inner\n"
        "\n"
        "class C:\n"
        "    def a(self):\n"
        "        pass\n"
    )
    assert find_duplicate_defs(source) == []


def test_route_handler_names_detects_decorated_handlers_only():
    source = (
        "@router.get('/a')\n"
        "async def list_items():\n"
        "    pass\n"
        "\n"
        "@router.post('/b')\n"
        "def create_item():\n"
        "    pass\n"
        "\n"
        "def _helper():\n"
        "    pass\n"
        "\n"
        "@staticmethod\n"
        "def not_a_route():\n"
        "    pass\n"
    )
    assert route_handler_names(source) == ["list_items", "create_item"]


def test_cross_module_handler_dupes_reports_shared_name():
    sources = {
        "jobs.py": "@router.get('/jobs')\nasync def list_items():\n    pass\n",
        "ideas.py": "@router.get('/ideas')\nasync def list_items():\n    pass\n",
        "misc.py": "def list_items():\n    pass\n",
    }
    assert find_cross_module_handler_dupes(sources) == {"list_items": ["ideas.py", "jobs.py"]}


def test_cross_module_handler_dupes_empty_when_unique():
    sources = {
        "jobs.py": "@router.get('/jobs')\nasync def list_jobs():\n    pass\n",
        "ideas.py": "@router.get('/ideas')\nasync def list_ideas():\n    pass\n",
    }
    assert find_cross_module_handler_dupes(sources) == {}
