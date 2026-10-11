import ast
from pathlib import Path


def test_routes_module_defines_each_function_once():
    # a second module-level `_job_payload` (scheduler) silently replaced the
    # builder-job one and broke builder-job dispatch
    tree = ast.parse(Path(__file__).resolve().parents[1].joinpath("backend/api/routes.py").read_text())
    names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"functions defined more than once in routes.py: {dupes}"
