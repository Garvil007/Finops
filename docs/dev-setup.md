# Development setup and repository configuration

Local setup lives in [CONTRIBUTING.md](../CONTRIBUTING.md). This page covers the
repository settings that CI and the publish workflow depend on, which have to be
configured once through the GitHub UI.

## Branch protection for `main`

Settings → Branches → Add branch ruleset (or Add rule for the classic UI), target
`main`:

| Setting | Value | Why |
| --- | --- | --- |
| Require a pull request before merging | on | Nothing lands on `main` unreviewed |
| Required approvals | 1 (0 for a solo project) | On a solo repo the PR itself is the gate; raise it the moment a second person contributes |
| Dismiss stale approvals on new commits | on | An approval describes the diff it was given for |
| Require status checks to pass | on | The point of the exercise |
| Required checks | `Lint`, `Typecheck`, `Test`, `Docker build` | The four CI job names |
| Require branches to be up to date | on | Prevents a semantic conflict merging green |
| Require conversation resolution | on | No merging over an open question |
| Require linear history | on | Keeps `git log` bisectable |
| Block force pushes | on | Default; leave it |
| Allow deletions | off | |

The required check names must match the `name:` of each job in
`.github/workflows/ci.yml` exactly. They only appear in the picker after the
workflow has run at least once, so push a branch and open a PR before configuring
this.

Setting required approvals to 1 on a solo repository blocks your own merges,
because GitHub does not let you approve your own PR. Either use 0 while working
alone, or add yourself to a bypass list.

## Secrets

Settings → Secrets and variables → Actions:

| Secret | Needed by | Notes |
| --- | --- | --- |
| `CODECOV_TOKEN` | The Codecov upload step in `ci.yml` | Optional. The step is `continue-on-error`, so CI stays green without it; the coverage gate and the job summary do not depend on Codecov |
| `GITHUB_TOKEN` | `docker-publish.yml` | Provided automatically; no action needed |

## Coverage badge

Coverage is enforced in two places that do not depend on any third party: the
`--cov-fail-under=80` gate in `pyproject.toml`, and the Markdown table written to
the CI job summary by `scripts/coverage_summary.py`.

The README badge uses Codecov, which needs one-time setup:

1. Sign in at <https://about.codecov.io> with GitHub and add the repository.
2. Copy the upload token into the `CODECOV_TOKEN` secret above.
3. The badge in `README.md` starts rendering after the first upload from `main`.

Until then the badge shows `unknown`. That is the honest state -- a badge
hardcoded to a number would be worse than no badge.

## Publishing an image

`docker-publish.yml` triggers on a `v*` tag and pushes to
`ghcr.io/garvil007/finopsai`.

```bash
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

The first push creates the package as **private**. To make it public: Packages →
`finopsai` → Package settings → Change visibility.

The workflow needs `packages: write`, which it declares. If the push fails with a
permissions error, check Settings → Actions → General → Workflow permissions is
set to read and write.

## Pinning the base image digest

The Dockerfile takes the base image as a build argument so it can be pinned
without editing the file:

```bash
docker buildx imagetools inspect python:3.12-slim-bookworm \
  --format '{{json .Manifest.Digest}}'

docker build --build-arg PYTHON_IMAGE=python@sha256:<digest> .
```

The committed default is the moving tag. Resolving a digest needs a registry
round trip, so substitute one and commit it when you next build.

## Dependency updates

There is no Dependabot configuration yet. Adding
`.github/dependabot.yml` for `pip`, `docker` and `github-actions` is the obvious
next step; it is left out rather than added untested.
