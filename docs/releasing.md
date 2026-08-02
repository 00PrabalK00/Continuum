# Releasing

Publishing is tag-driven. Pushing `vX.Y.Z` builds, checks and publishes to both
registries; nothing publishes on a merge to `main`.

Nothing has been published yet. The workflow and metadata are ready, but the
one-time setup below has to be done by someone with owner access to the
accounts, and it cannot be done from CI.

## One-time setup

**PyPI, trusted publishing.** No API token is stored anywhere. PyPI verifies the
workflow's identity instead, which means a leaked repository secret cannot be
used to publish.

1. On [pypi.org](https://pypi.org/manage/account/publishing/), add a pending
   publisher for the project name `continuum-agent-memory`.
2. Owner `00PrabalK00`, repository `Continuum`, workflow `release.yml`,
   environment `release`.

**npm.** npm has no equivalent of trusted publishing for this case, so a token
is required.

1. Create an automation token on npmjs.com with publish rights.
2. Add it as the repository secret `NPM_TOKEN`.

**GitHub.** Create an environment named `release` under repository settings.
Adding required reviewers to it makes every publish a deliberate approval rather
than a consequence of pushing a tag.

## Cutting a release

1. Update `CHANGELOG.md`.
2. Set the same version in `pyproject.toml`, `package.json` and
   `continuum/__init__.py`. `tests/test_packaging.py` fails if they disagree,
   and the workflow refuses to publish if the tag disagrees with any of them.
3. Merge to `main`.
4. Tag and push:

   ```bash
   git tag v0.13.0
   git push origin v0.13.0
   ```

The workflow runs the full matrix again before publishing. A tag can point at
any commit, so the tests from the merge are not taken on trust.

## Checking without publishing

Run the workflow manually from the Actions tab with `dry_run` left on. It
builds, runs `twine check` and `npm pack --dry-run`, and publishes nothing.

Locally, the same checks:

```bash
python -m build
python -m twine check dist/*
npm pack --dry-run
```

## After the first publish

`README.md` and `docs/quickstart.md` still tell people to clone the repository,
because that is currently the only way to install Continuum. Once both registries
have accepted a release, replace those instructions with:

```bash
pip install continuum-agent-memory
# or
npx -y continuum-agent-memory@latest install
```

Do that after verifying both in a clean environment, not before. Documentation
that describes an install path nobody has tested is how the current
"after npm publishing is complete" wording ended up outliving several releases.
