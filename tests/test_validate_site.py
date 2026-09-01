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
