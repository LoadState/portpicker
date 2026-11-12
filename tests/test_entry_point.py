"""app.py must start the UI when run (PyInstaller runs it as __main__). PR #7 dropped this and the exe exited silently."""
import ast
from pathlib import Path


def test_app_calls_main_when_run():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text("utf-8"))
    guards = [n for n in tree.body if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test)]
    assert guards, "app.py has no `if __name__ == \"__main__\":` block"
    assert any("main()" in ast.unparse(s) for s in guards[0].body), "the __main__ block does not call main()"
