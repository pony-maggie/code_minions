# Release

Manual release steps for maintainers.

## 0. Prereqs

- You have Owner permissions on the PyPI project `code-minions`
- `.pypirc` configured with tokens for both TestPyPI and PyPI
- `build` + `twine` installed: `pip install build twine`

## 1. Bump version + CHANGELOG

- Update `version` in `pyproject.toml`
- Add the new section to `CHANGELOG.md`
- Commit: `git commit -m "chore(release): vX.Y.Z"`

## 2. Dry run (TestPyPI)

```bash
rm -rf dist/
python -m build
twine check dist/*
twine upload --repository testpypi dist/*
```

Verify in a fresh venv:

```bash
python -m venv /tmp/cm-test
/tmp/cm-test/bin/pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  code-minions
/tmp/cm-test/bin/code-minions --help
```

## 3. Tag + push

```bash
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

## 4. Publish to PyPI

```bash
twine upload dist/*
```

## 5. GitHub Release

```bash
gh release create vX.Y.Z --notes-file <(awk '/^## /{p++}p==1' CHANGELOG.md | tail -n +2)
```

Or manually via the GitHub web UI, pasting the relevant CHANGELOG section.

## 6. Announce (optional)

- Twitter / X / Mastodon
- HackerNews (only for 1.0.0+)
- Project Discord/Slack

## Rollback

If a release is broken:

1. Yank from PyPI: `twine upload --skip-existing ...` won't help; use `pip install twine-yank` or the PyPI web UI
2. Publish a patch release with the fix; do not delete the broken version (it may already be cached)
