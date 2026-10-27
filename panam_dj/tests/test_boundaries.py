import ast
from pathlib import Path
import sys
import unittest


class BoundaryTests(unittest.TestCase):
    def test_only_stdlib_discord_and_dj_imports(self):
        root = Path(__file__).resolve().parents[1]
        allowed = sys.stdlib_module_names | {"discord", "panam_dj"}
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    modules = [node.module]
                for module in modules:
                    with self.subTest(path=path.name, module=module):
                        self.assertIn(module.split(".")[0], allowed)

    def test_declared_dependency_family_is_independent(self):
        root = Path(__file__).resolve().parents[1]
        requirements = [line for line in (root / "requirements.txt").read_text().splitlines()
                        if line and not line.startswith("#")]
        self.assertEqual(requirements, ["discord.py[voice]>=2.7.1,<3"])
