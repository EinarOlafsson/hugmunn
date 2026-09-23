# Repository preferences

- Make changes on `nightly`. Commit and push each finished, validated unit
  promptly, with useful descriptions. Merge validated work into `main`.
- `main` is the default and release branch. Bump `src/hugmunn/_version.py` and
  update `CHANGELOG.md` on `nightly` before merging a new release. A main push
  with an unreleased version triggers checks, desktop builds, PyPI publication,
  and a GitHub release. Versions already on both services are not republished.
- Use the repository owner's identity for commits: Einar Olafsson
  <einar.olafsson@gmail.com>. Do not add assistant, bot, or co-author identities.
- Write commit messages that explain the problem, the resulting change, and
  relevant validation. Keep titles specific and put details in the body.
- Publish Hugmunn through Einar's PyPI account. Prefer the trusted publisher
  for `EinarOlafsson/hugmunn`, workflow `publish.yml`, environment `pypi`.
  Never print or commit credentials.
- Keep README instructions practical and consistent with the shipped code.
  Update API docstrings and user documentation when behavior changes.
- Use the raven artwork in `src/hugmunn/resources/icons/`, choosing black for
  light backgrounds and white for dark backgrounds.
