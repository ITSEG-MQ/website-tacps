#!/usr/bin/env python3
"""Validate the source and generated output of the static Jekyll sites."""

from __future__ import annotations

import argparse
import ast
from html.parser import HTMLParser
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ACTION_PIN = re.compile(r"^[0-9a-f]{40}$")
CSS_URL = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.IGNORECASE)
FORBIDDEN_OUTPUTS = {
    ".github",
    "LICENSE",
    "README.md",
    "editor-handbook.md",
    "maintainer-handbook.md",
    "scripts",
    "tests",
}
REQUIRED_EXCLUDES = {
    "LICENSE",
    "README.md",
    "editor-handbook.md",
    "maintainer-handbook.md",
    "scripts",
    "tests",
}
FORBIDDEN_SUFFIXES = {".bak", ".orig", ".rej"}


class ReferenceParser(HTMLParser):
    """Collect URL-bearing HTML attributes with source line numbers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[str, int]] = []
        self.includes: list[tuple[str, int]] = []
        self.noindex = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        line, _ = self.getpos()
        normalized = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta" and normalized.get("name", "").lower() == "robots":
            self.noindex = "noindex" in normalized.get("content", "").lower()
        for key, value in attrs:
            if not value:
                continue
            if key in {"href", "src", "poster", "data-src"}:
                self.references.append((value.strip(), line))
            elif key == "w3-include-html":
                include = value.strip()
                self.includes.append((include, line))
                self.references.append((include, line))
            elif key == "srcset":
                for candidate in value.split(","):
                    url = candidate.strip().split(" ", 1)[0]
                    if url:
                        self.references.append((url, line))


def scalar(value: str) -> str:
    """Parse a simple YAML scalar without requiring PyYAML."""

    value = value.strip()
    if not value:
        return ""
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    if value[:1] in {"'", '"'}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value.strip("'\"")
        return str(parsed)
    return value


def load_config(path: Path) -> dict[str, str]:
    """Read the top-level scalar keys used by these Jekyll configs."""

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line[0].isspace() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if match:
            values[match.group(1)] = scalar(match.group(2))
    return values


def load_list(path: Path, key: str) -> list[str]:
    """Read one simple top-level YAML list."""

    result: list[str] = []
    active = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not active:
            if re.match(rf"^{re.escape(key)}:\s*$", line):
                active = True
            continue
        if line and not line[0].isspace():
            break
        match = re.match(r"^\s+-\s+(.+?)\s*$", line)
        if match:
            result.append(scalar(match.group(1)))
    return result


def workflow_action_errors(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    for line_number, line in enumerate(text.splitlines(), 1):
        match = re.search(r"\buses:\s*([^\s#]+)", line)
        if not match:
            continue
        action = match.group(1)
        if action.startswith(("./", "docker://")):
            continue
        if "@" not in action:
            errors.append(f"{path.name}:{line_number}: action has no revision: {action}")
            continue
        revision = action.rsplit("@", 1)[1]
        if not ACTION_PIN.fullmatch(revision):
            errors.append(
                f"{path.name}:{line_number}: action is not pinned to a 40-character commit SHA: {action}"
            )
    return errors


def workflow_policy(text: str) -> str:
    """Remove YAML comments while preserving hash characters inside quotes."""

    result: list[str] = []
    for line in text.splitlines():
        output: list[str] = []
        quote = ""
        escaped = False
        for character in line:
            if escaped:
                output.append(character)
                escaped = False
                continue
            if quote == '"' and character == "\\":
                output.append(character)
                escaped = True
                continue
            if quote:
                output.append(character)
                if character == quote:
                    quote = ""
                continue
            if character in {"'", '"'}:
                quote = character
                output.append(character)
            elif character == "#":
                break
            else:
                output.append(character)
        cleaned = "".join(output).rstrip()
        if cleaned:
            result.append(cleaned)
    return "\n".join(result)


def top_level_block(policy: str, key: str) -> str:
    """Return one top-level YAML mapping block from comment-free workflow text."""

    lines = policy.splitlines()
    for index, line in enumerate(lines):
        if line == f"{key}:":
            block = [line]
            for following in lines[index + 1 :]:
                if following and not following[0].isspace():
                    break
                block.append(following)
            return "\n".join(block)
    return ""


def source_errors(root: Path) -> list[str]:
    errors: list[str] = []
    staging_path = root / "_config.yml"
    production_path = root / "_config_prod.yml"
    release_workflow = root / ".github/workflows/release-zip.yml"
    validate_workflow = root / ".github/workflows/validate.yml"

    for path in (staging_path, production_path, release_workflow, validate_workflow):
        if not path.is_file():
            errors.append(f"required source file is missing: {path.relative_to(root)}")
    if errors:
        return errors

    staging = load_config(staging_path)
    production = load_config(production_path)
    if staging.get("environment") != "staging":
        errors.append("_config.yml must set environment: staging")
    if not staging.get("url", "").startswith("https://"):
        errors.append("_config.yml must use an https URL")
    if not staging.get("baseurl", "").startswith("/website-"):
        errors.append("_config.yml must use the repository /website-* baseurl")
    if production.get("environment") != "production":
        errors.append("_config_prod.yml must set environment: production")
    if not production.get("url", "").startswith("https://"):
        errors.append("_config_prod.yml must use an https URL")
    if "baseurl" not in production or production.get("baseurl") != "":
        errors.append("_config_prod.yml must use an empty baseurl")

    for config_path in (staging_path, production_path):
        excludes = set(load_list(config_path, "exclude"))
        missing = sorted(REQUIRED_EXCLUDES - excludes)
        if missing:
            errors.append(f"{config_path.name} is missing excludes: {', '.join(missing)}")

    release_text = release_workflow.read_text(encoding="utf-8")
    validate_text = validate_workflow.read_text(encoding="utf-8")
    release_policy = workflow_policy(release_text)
    validate_policy = workflow_policy(validate_text)
    release_triggers = top_level_block(release_policy, "on")
    validate_triggers = top_level_block(validate_policy, "on")
    release_permissions = top_level_block(release_policy, "permissions")
    validate_permissions = top_level_block(validate_policy, "permissions")
    errors.extend(workflow_action_errors(release_workflow))
    errors.extend(workflow_action_errors(validate_workflow))
    if "concurrency:" not in release_policy or "group: production-release" not in release_policy:
        errors.append("release-zip.yml must serialize production releases")
    if 'branches: ["main"]' not in release_triggers or not re.fullmatch(
        r"permissions:\n\s+contents:\s*write", release_permissions
    ):
        errors.append("release-zip.yml must run from main with release write permission")
    if "if: github.ref == 'refs/heads/main'" not in release_policy:
        errors.append("release-zip.yml must guard manual releases to refs/heads/main")
    if "overwrite_files: true" not in release_policy:
        errors.append("release-zip.yml must explicitly overwrite the rolling site.zip asset")
    if "replace_assets:" in release_policy:
        errors.append("release-zip.yml contains the unsupported replace_assets input")
    if "scripts/validate_site.py --site _site" not in release_policy:
        errors.append("release-zip.yml must validate generated output before packaging")
    if not re.fullmatch(r"permissions:\n\s+contents:\s*read", validate_permissions):
        errors.append("validate.yml must use read-only repository permissions")
    for trigger in ('"feature/**"', '"feat/**"', "pull_request:", "workflow_dispatch:"):
        if trigger not in validate_triggers:
            errors.append(f"validate.yml is missing required trigger: {trigger}")
    if "actions/upload-artifact@" not in validate_policy:
        errors.append("validate.yml must upload a preview artifact")
    if "scripts/validate_site.py --site _site" not in validate_policy:
        errors.append("validate.yml must validate generated output")
    return errors


def is_external(reference: str, expected_host: str) -> tuple[bool, str]:
    parsed = urlsplit(reference)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return True, ""
    if parsed.netloc and parsed.hostname != expected_host:
        return True, ""
    if parsed.scheme in {"http", "https"} and parsed.hostname != expected_host:
        return True, ""
    return False, unquote(parsed.path)


def resolve_reference(site: Path, source: Path, path: str) -> Path | None:
    if not path:
        return None
    candidate = site / path.lstrip("/") if path.startswith("/") else source.parent / path
    try:
        candidate = candidate.resolve()
        candidate.relative_to(site.resolve())
    except (OSError, ValueError):
        return Path("/__outside_site__")
    if path.endswith("/") or candidate.is_dir():
        return candidate / "index.html"
    if candidate.exists():
        return candidate
    if not candidate.suffix:
        return candidate / "index.html"
    return candidate


def generated_errors(site: Path, config_path: Path) -> list[str]:
    errors: list[str] = []
    site = site.resolve()
    config_path = config_path.resolve()
    if not site.is_dir():
        return [f"generated site directory is missing: {site}"]
    config = load_config(config_path)
    expected_url = config.get("url", "")
    expected_host = urlsplit(expected_url).hostname or ""

    for required in ("index.html", "robots.txt"):
        if not (site / required).is_file():
            errors.append(f"generated site is missing {required}")
    for name in sorted(FORBIDDEN_OUTPUTS):
        if (site / name).exists():
            errors.append(f"development-only output was published: {name}")

    for path in site.rglob("*"):
        if path.is_symlink():
            errors.append(f"generated site contains a symlink: {path.relative_to(site)}")
        if path.is_file() and path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"backup or patch artifact was published: {path.relative_to(site)}")

    parsed_pages: dict[Path, tuple[str, ReferenceParser]] = {}
    for html_path in sorted(site.rglob("*.html")):
        text = html_path.read_text(encoding="utf-8", errors="replace")
        parser = ReferenceParser()
        parser.feed(text)
        parsed_pages[html_path] = (text, parser)

    include_contexts: dict[Path, set[Path]] = {}
    for parent, (_, parser) in parsed_pages.items():
        for reference, _ in parser.includes:
            external, reference_path = is_external(reference, expected_host)
            if external:
                continue
            target = resolve_reference(site, parent, reference_path)
            if target is not None and target.exists():
                include_contexts.setdefault(target, set()).add(parent)

    for html_path, (text, parser) in parsed_pages.items():
        relative = html_path.relative_to(site)
        if "itseg-mq.github.io" in text:
            errors.append(f"{relative}: production HTML contains the staging host")
        if parser.noindex:
            errors.append(f"{relative}: production HTML unexpectedly contains noindex")
        contexts = include_contexts.get(html_path, {html_path})
        for reference, line in parser.references:
            if not reference or reference.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                continue
            external, reference_path = is_external(reference, expected_host)
            if external:
                continue
            for context in contexts:
                target = resolve_reference(site, context, reference_path)
                if target is not None and not target.exists():
                    context_note = ""
                    if context != html_path:
                        context_note = f" when included from {context.relative_to(site)}"
                    errors.append(
                        f"{relative}:{line}: missing internal reference {reference!r}{context_note}"
                    )

    for css_path in sorted(site.rglob("*.css")):
        text = css_path.read_text(encoding="utf-8", errors="replace")
        relative = css_path.relative_to(site)
        for _, reference in CSS_URL.findall(text):
            reference = reference.strip()
            if not reference or reference.startswith(("#", "data:")):
                continue
            external, reference_path = is_external(reference, expected_host)
            if external:
                continue
            target = resolve_reference(site, css_path, reference_path)
            if target is not None and not target.exists():
                errors.append(f"{relative}: missing CSS reference {reference!r}")

    robots_path = site / "robots.txt"
    if robots_path.is_file():
        robots = robots_path.read_text(encoding="utf-8", errors="replace")
        if "Allow: /" not in robots or "Disallow: /" in robots:
            errors.append("production robots.txt must allow crawling")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--site", type=Path)
    parser.add_argument("--config", type=Path, default=Path("_config_prod.yml"))
    args = parser.parse_args()

    root = args.root.resolve()
    if args.site:
        site = args.site if args.site.is_absolute() else root / args.site
        config = args.config if args.config.is_absolute() else root / args.config
        errors = generated_errors(site.resolve(), config.resolve())
        label = "Generated-site"
    else:
        errors = source_errors(root)
        label = "Source"

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"{label} validation failed with {len(errors)} error(s).", file=sys.stderr)
        return 1
    print(f"{label} validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
