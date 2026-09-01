from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_site.py"
SPEC = importlib.util.spec_from_file_location("site_validator", SCRIPT)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class GeneratedSiteValidationTests(unittest.TestCase):
    def write(self, root: Path, relative: str, content: str = "") -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def valid_fixture(self, root: Path) -> tuple[Path, Path]:
        site = root / "site"
        config = self.write(
            root,
            "_config_prod.yml",
            'title: "Example"\nurl: "https://example.org"\nbaseurl: ""\nenvironment: "production"\n',
        )
        self.write(
            site,
            "index.html",
            '<!doctype html><html lang="en"><head><title>Home</title>'
            '<link rel="stylesheet" href="/assets/main.css"></head>'
            '<body><a href="/about/">About</a></body></html>',
        )
        self.write(site, "about/index.html", "<!doctype html><title>About</title>")
        self.write(site, "assets/main.css", 'body { background: url("/assets/logo.png"); }')
        self.write(site, "assets/logo.png", "not-a-real-image")
        self.write(site, "robots.txt", "User-agent: *\nAllow: /\n")
        return site, config

    def source_fixture(self, root: Path) -> None:
        repository = SCRIPT.parents[1]
        for relative in (
            "_config.yml",
            "_config_prod.yml",
            ".github/workflows/release-zip.yml",
            ".github/workflows/validate.yml",
        ):
            self.write(root, relative, (repository / relative).read_text(encoding="utf-8"))

    def test_valid_generated_site_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site, config = self.valid_fixture(Path(temporary))
            self.assertEqual([], validator.generated_errors(site, config))

    def test_missing_html_reference_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site, config = self.valid_fixture(Path(temporary))
            self.write(site, "index.html", '<a href="/missing/">Missing</a>')
            errors = validator.generated_errors(site, config)
            self.assertTrue(any("missing internal reference" in error for error in errors), errors)

    def test_missing_css_reference_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site, config = self.valid_fixture(Path(temporary))
            self.write(site, "assets/main.css", 'body { background: url("/assets/missing.png"); }')
            errors = validator.generated_errors(site, config)
            self.assertTrue(any("missing CSS reference" in error for error in errors), errors)

    def test_development_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site, config = self.valid_fixture(Path(temporary))
            self.write(site, "README.md", "development documentation")
            errors = validator.generated_errors(site, config)
            self.assertTrue(any("development-only output" in error for error in errors), errors)

    def test_backup_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site, config = self.valid_fixture(Path(temporary))
            self.write(site, "archive/index.html.bak", "stale backup")
            errors = validator.generated_errors(site, config)
            self.assertTrue(any("backup or patch artifact" in error for error in errors), errors)

    def test_staging_host_and_noindex_fail_in_production(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site, config = self.valid_fixture(Path(temporary))
            self.write(
                site,
                "index.html",
                '<meta name="robots" content="noindex,nofollow">'
                '<a href="https://itseg-mq.github.io/website-example/">Staging</a>',
            )
            errors = validator.generated_errors(site, config)
            self.assertTrue(any("staging host" in error for error in errors), errors)
            self.assertTrue(any("noindex" in error for error in errors), errors)

    def test_noindex_detection_is_attribute_order_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site, config = self.valid_fixture(Path(temporary))
            self.write(site, "index.html", '<meta content="nofollow,noindex" name="robots">')
            errors = validator.generated_errors(site, config)
            self.assertTrue(any("noindex" in error for error in errors), errors)

    def test_dynamic_include_references_use_parent_document_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site, config = self.valid_fixture(Path(temporary))
            self.write(
                site,
                "section/index.html",
                '<div w3-include-html="./html/card.html"></div>',
            )
            self.write(site, "section/html/card.html", '<img src="images/photo.png">')
            self.write(site, "section/images/photo.png", "not-a-real-image")
            self.assertEqual([], validator.generated_errors(site, config))

            self.write(site, "section/html/card.html", '<img src="../images/photo.png">')
            errors = validator.generated_errors(site, config)
            self.assertTrue(any("when included from section/index.html" in error for error in errors), errors)

    def test_manual_release_requires_main_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.source_fixture(root)
            release = root / ".github/workflows/release-zip.yml"
            release.write_text(
                release.read_text(encoding="utf-8").replace(
                    "    if: github.ref == 'refs/heads/main'\n", ""
                ),
                encoding="utf-8",
            )
            errors = validator.source_errors(root)
            self.assertTrue(any("guard manual releases" in error for error in errors), errors)

    def test_commented_permission_text_cannot_spoof_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.source_fixture(root)
            workflow = root / ".github/workflows/validate.yml"
            workflow.write_text(
                workflow.read_text(encoding="utf-8").replace(
                    "permissions:\n  contents: read",
                    "permissions: write-all # contents: read",
                ),
                encoding="utf-8",
            )
            errors = validator.source_errors(root)
            self.assertTrue(any("read-only repository permissions" in error for error in errors), errors)

    def test_workflow_comment_stripping_preserves_quoted_hash(self) -> None:
        policy = validator.workflow_policy('name: "value # retained" # removed\n# contents: read')
        self.assertEqual('name: "value # retained"', policy)

    def test_source_config_parser_reads_scalars_and_lists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self.write(
                Path(temporary),
                "_config.yml",
                'url: "https://example.org" # comment\nbaseurl: ""\nexclude:\n  - README.md\n  - tests\n',
            )
            self.assertEqual("https://example.org", validator.load_config(config)["url"])
            self.assertEqual(["README.md", "tests"], validator.load_list(config, "exclude"))


if __name__ == "__main__":
    unittest.main()
