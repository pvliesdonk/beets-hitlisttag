# Contributing

## Development setup

Requires Python 3.10–3.14.

    python -m venv .venv
    source .venv/bin/activate
    pip install -e '.[test]'

## Checks

CI runs these on every pull request and on pushes to `main`, across Python
3.10–3.14. Run them locally before pushing:

    ruff check .
    ruff format --check .
    pytest -q

`ruff format .` applies formatting; `ruff check --fix .` applies safe lint fixes.

## Commits

This project uses [Conventional Commits](https://www.conventionalcommits.org/)
(`type: summary`, with types `feat`, `fix`, `docs`, `test`, `build`, `ci`,
`chore`, …). Keep the habit: small, conventional commits make history and
review easier.

## Pull requests

- One pull request at a time off `main`, each closing its own issue, merged
  before the next begins. No stacking.
- Keep diffs small and reviewable — the roadmap sequences the work into small
  PRs on purpose.
- Add or update tests with the change.

## Releases

The version comes from the git tag via `hatch-vcs`. Publishing a GitHub Release
runs `release.yml`, which builds the package and publishes it to PyPI via
[trusted publishing](https://docs.pypi.org/trusted-publishers/) (OIDC) — no API
token is stored in the repository.

### One-time setup

These prerequisites are done once, manually, outside the repository.

**GitHub environment.** In the repository's *Settings → Environments*, create an
environment named `pypi`. Adding a required reviewer or restricting it to the
`main` branch is optional but recommended — it gates the publish step.

**PyPI trusted publisher.** The PyPI project does not need to exist yet; a
"pending publisher" creates it on first use.

1. Sign in to [PyPI](https://pypi.org) and open *Account settings →
   Publishing* (`https://pypi.org/manage/account/publishing/`).
2. Under *Add a new publisher*, select **GitHub**.
3. Fill in:
   - **PyPI project name**: `beets-hitlisttag`
   - **Repository owner**: `pvliesdonk`
   - **Repository name**: `beets-hitlisttag`
   - **Workflow filename**: `release.yml`
   - **Environment name**: `pypi`
4. Click **Add**.

The project name is not reserved until the first publish, so cut the first
release promptly. The package name in `pyproject.toml` must match the PyPI
project name exactly. On the first successful workflow run, PyPI converts the
pending publisher into a normal one and creates the project.

If the project already exists on PyPI, configure the publisher from the
project's *Manage → Publishing* page instead, filling in the same repository
owner, repository name, workflow filename, and environment name.

### Cutting a release

1. Ensure `main` is green and at the commit to release.
2. Tag it: `git tag -a vX.Y.Z -m " vX.Y.Z"`.
3. Push the tag: `git push origin vX.Y.Z`.
4. Create a GitHub Release from the tag (web UI or `gh release create`).

Publishing the release triggers `release.yml`, which builds the package and
publishes it to PyPI. There is no separate changelog generation; describe the
changes in the GitHub Release notes.