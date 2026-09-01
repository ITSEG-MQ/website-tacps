# TACPS website maintainer handbook

## Repository structure

- `_config.yml` — GitHub Pages staging URL and base path.
- `_config_prod.yml` — production URL and empty base path.
- `_includes/` and `_layouts/` — shared page chrome and layout.
- `assets/` — reusable styles and images.
- `archive/`, `program/`, `shonan235/`, and other page directories — current and archived public content.
- `editor-handbook.md` — content-editing guidance.
- `scripts/validate_site.py` — source and generated-site validation.
- `tests/` — validator regression tests.
- `.github/workflows/validate.yml` — feature-branch and pull-request build validation.
- `.github/workflows/release-zip.yml` — validated rolling production release.

## Local validation

Run from the repository root:

```bash
python3 scripts/validate_site.py
python3 -m unittest discover -s tests -v
```

The GitHub validation workflow is the authoritative production-config Jekyll build. It uploads the generated `_site` directory as a seven-day `site-preview` artifact.

## Deployment

- Staging: https://itseg-mq.github.io/website-tacps/
- Production: https://tacps.org/
- Feature branches matching `feature/**` or `feat/**`, pull requests, and manual dispatches run `validate.yml` without publishing a release.
- A push to `main` runs source tests, builds with `_config_prod.yml`, validates generated links and assets, and updates `site.zip` on the rolling `prod` release.
- The production server polls that release and deploys a new asset automatically.

Do not commit `_site`, local caches, ZIP artifacts, credentials, or deployment tokens.
