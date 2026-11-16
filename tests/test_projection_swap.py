# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rag_gateway.projection import install_metadata, swap_projection


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class ProjectionSwapTest(unittest.TestCase):
    def _make_live(self, root: Path, label: str) -> None:
        _write(root / "rag/qmd/core/item.md", label)
        _write(root / "rag/qmd/evidence/item.md", label)
        _write(root / "rag/extracted/item.md", label)
        _write(root / "rag/manifest.json", json.dumps({"label": label}))
        _write(root / "rag/graph.json", json.dumps({"label": label}))
        _write(root / "rag/lexicon.json", json.dumps({"label": label}))

    def _make_staging(self, staging: Path, label: str) -> None:
        _write(staging / "qmd/core/item.md", label)
        _write(staging / "qmd/evidence/item.md", label)
        _write(staging / "extracted/item.md", label)
        _write(staging / "manifest.json", json.dumps({"label": label}))
        _write(staging / "graph.json", json.dumps({"label": label}))
        _write(staging / "lexicon.json", json.dumps({"label": label}))

    def test_swap_installs_new_and_keeps_old_backup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            staging = root / "rag/.projection-staging-test"
            self._make_live(root, "old")
            self._make_staging(staging, "new")
            previous = swap_projection(staging, root)

            self.assertEqual((root / "rag/qmd/core/item.md").read_text(encoding="utf-8"), "new")
            self.assertEqual(json.loads((root / "rag/manifest.json").read_text(encoding="utf-8"))["label"], "new")
            self.assertTrue((previous / "manifest.json").exists())
            self.assertEqual((previous / "qmd/core/item.md").read_text(encoding="utf-8"), "old")
            self.assertFalse(staging.exists())

    def test_deferred_metadata_can_be_installed_after_qmd(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            staging = root / "rag/.projection-staging-test"
            self._make_live(root, "old")
            self._make_staging(staging, "new")
            previous = swap_projection(staging, root, defer_metadata=True)

            # Projection dirs are new, but the old manifest stays active while
            # QMD update/embed would run.
            self.assertEqual((root / "rag/qmd/core/item.md").read_text(encoding="utf-8"), "new")
            self.assertEqual(json.loads((root / "rag/manifest.json").read_text(encoding="utf-8"))["label"], "old")
            self.assertTrue((staging / "manifest.json").exists())

            install_metadata(staging, root, previous)
            self.assertEqual(json.loads((root / "rag/manifest.json").read_text(encoding="utf-8"))["label"], "new")
            self.assertEqual(json.loads((previous / "manifest.json").read_text(encoding="utf-8"))["label"], "old")
            self.assertFalse(staging.exists())

    def test_swap_failure_restores_live_tree(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            staging = root / "rag/.projection-staging-test"
            self._make_live(root, "old")
            # Missing manifest must fail after directories have been moved;
            # the helper must restore the old live tree.
            _write(staging / "qmd/core/item.md", "new")
            _write(staging / "qmd/evidence/item.md", "new")
            _write(staging / "extracted/item.md", "new")
            _write(staging / "graph.json", json.dumps({"label": "new"}))
            _write(staging / "lexicon.json", json.dumps({"label": "new"}))

            with self.assertRaises(FileNotFoundError):
                swap_projection(staging, root)

            self.assertEqual((root / "rag/qmd/core/item.md").read_text(encoding="utf-8"), "old")
            self.assertEqual((root / "rag/manifest.json").read_text(encoding="utf-8"), '{"label": "old"}')


if __name__ == "__main__":
    unittest.main()
