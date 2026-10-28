"""Every tr() string has an English translation with the same placeholders."""

import ast
import glob
import os
import string
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from switchtk import i18n  # noqa: E402
from switchtk.i18n_en import EN  # noqa: E402


def tr_keys():
    keys, problems = set(), []
    for path in glob.glob(os.path.join(ROOT, "switchtk", "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr":
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    keys.add(arg.value)
                else:
                    problems.append(f"{os.path.relpath(path, ROOT)}:{node.lineno}")
    return keys, problems


def fields(text):
    return {f for _, f, _, _ in string.Formatter().parse(text) if f is not None}


class Translations(unittest.TestCase):
    def test_every_string_is_translated(self):
        keys, problems = tr_keys()
        self.assertEqual(problems, [], "tr() must get a string literal")
        missing = sorted(keys - EN.keys())
        self.assertEqual(missing, [], "missing English translations")
        unused = sorted(EN.keys() - keys)
        self.assertEqual(unused, [], "translations of strings no longer used")

    def test_placeholders_match(self):
        for it, en in EN.items():
            self.assertEqual(fields(it), fields(en), it)

    def test_switching_language(self):
        try:
            i18n.set_language("en")
            self.assertEqual(i18n.tr("Creo {name}", name="a.xci"), "Creating a.xci")
            i18n.set_language("it")
            self.assertEqual(i18n.tr("Creo {name}", name="a.xci"), "Creo a.xci")
        finally:
            i18n.set_language("it")
        self.assertIn(i18n.resolve("auto"), ("it", "en"))
        self.assertEqual(i18n.resolve("en"), "en")


if __name__ == "__main__":
    unittest.main()
