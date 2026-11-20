"""Tests for deterministic .zip product packaging, toolkit test gates, and storefront copy."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from products.builder import ProductBuildError, build_all_products


class TestDigitalProductBuilder(unittest.TestCase):
    def test_builds_deterministic_sha256_zip_archives_and_storefront_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "repo"
            shutil.copytree(PROJECT_ROOT / "products", workspace / "products")
            # Remove any pre-existing dist inside temp copy
            shutil.rmtree(workspace / "products" / "dist", ignore_errors=True)

            first = build_all_products(
                workspace,
                kofi_profile_url="https://ko-fi.com/tharushanuwansara",
                primary_store_url="https://ko-fi.com/tharushanuwansara/shop",
                mirror_store_url="https://polar.sh/tharushanuwansara",
            )
            self.assertEqual(len(first["products"]), 3)

            second = build_all_products(
                workspace,
                kofi_profile_url="https://ko-fi.com/tharushanuwansara",
                primary_store_url="https://ko-fi.com/tharushanuwansara/shop",
                mirror_store_url="https://polar.sh/tharushanuwansara",
            )

            for p1, p2 in zip(first["products"], second["products"]):
                self.assertEqual(p1["sha256"], p2["sha256"], "ZIP SHA-256 must be 100% deterministic")
                self.assertGreater(p1["size_bytes"], 1000)
                self.assertTrue(p1["python_tests_passed"])
                with zipfile.ZipFile(p1["zip_path"], "r") as zf:
                    self.assertIsNone(zf.testzip())
                    names = zf.namelist()
                    self.assertTrue(all(n.startswith(f"{p1['slug']}/") for n in names))
                    self.assertFalse(any("__pycache__" in n or n.endswith(".pyc") for n in names))

            copy_md = Path(first["storefront_copy_path"]).read_text(encoding="utf-8")
            self.assertIn("https://ko-fi.com/tharushanuwansara", copy_md)
            self.assertIn("https://polar.sh/tharushanuwansara", copy_md)
            self.assertIn("free-idempotent-cron-mini-kit.zip", copy_md)
            self.assertIn("production-agent-automation-kit.zip", copy_md)

    def test_refuses_to_package_when_toolkit_unit_test_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "broken_repo"
            shutil.copytree(PROJECT_ROOT / "products", workspace / "products")
            shutil.rmtree(workspace / "products" / "dist", ignore_errors=True)

            # Inject a failing assertion into the temp copy of test_mini_kit.py
            mini_test = workspace / "products" / "toolkits" / "free_cron_mini_kit" / "test_mini_kit.py"
            mini_test.write_text(
                mini_test.read_text(encoding="utf-8")
                + "\nclass InjectedBrokenTest(unittest.TestCase):\n    def test_boom(self):\n        self.fail('injected regression')\n",
                encoding="utf-8",
            )

            with self.assertRaises(ProductBuildError):
                build_all_products(workspace)


if __name__ == "__main__":
    unittest.main()
