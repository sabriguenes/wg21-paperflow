---
name: release
description: Bump version in pyproject files, tag, push, and create a GitHub release with notes drafted from commits since the last tag.
disable-model-invocation: true
argument-hint: "[major|minor|patch|<explicit version>]"
---

# Cut a Release

Bump the version, tag, push, and create a GitHub release.

## Steps

1. **Resolve the new version.**
   - Run `git describe --tags --abbrev=0` to find the previous tag (e.g. `v0.2.1`).
   - Read the current version from the root `pyproject.toml`.
   - The argument is `major`, `minor`, `patch`, or an explicit `X.Y.Z`. If no argument was provided, ask the user.
   - Compute the new version (no `v` prefix in `pyproject.toml`, `v` prefix on the git tag).

2. **Sanity checks before touching anything.**
   - `git status --porcelain` must be empty. If not, stop and ask.
   - Current branch should be `main` and up to date with `origin/main`. If not, stop and ask.
   - `git tag -l v<new>` must be empty. If the tag exists, stop.

3. **Bump versions.** Update `version = "..."` in:
   - Root `pyproject.toml`.
   - Every `packages/*/pyproject.toml` whose current version equals the root's previous version. Leave packages that intentionally diverge (e.g. unreleased sub-packages on `0.1.0`) alone unless the user says otherwise — show the user which packages will move and which will not before editing.

4. **Run the workspace `uv sync`** to refresh `uv.lock`, then `uv run pytest -q` as a smoke check. If tests fail, stop.

5. **Draft release notes.** Collect input from:
   - `git log --oneline <prev-tag>..HEAD`
   - `gh pr list --state merged --search "merged:>$(git log -1 --format=%cI <prev-tag>)" --limit 50` for PR titles + authors.

   Produce a markdown body matching the project's style (see `gh release view <prev-tag>` for the most recent example). At minimum:
   ```
   ## v<new>

   <one-paragraph summary>

   ### Highlights
   - ...

   ## What's Changed
   * <PR title> by @<author> in <PR url>

   **Full Changelog**: https://github.com/<owner>/<repo>/compare/v<prev>...v<new>
   ```
   Show the draft to the user and wait for approval before continuing.

6. **Commit, tag, push.** After approval:
   ```
   git add pyproject.toml packages/*/pyproject.toml uv.lock
   git commit -m "Release v<new>"
   git tag -a v<new> -m "v<new>"
   git push origin main
   git push origin v<new>
   ```

7. **Create the release.**
   ```
   gh release create v<new> --title "v<new>" --notes-file <tmpfile>
   ```
   Then print the release URL from `gh release view v<new> --json url -q .url`.

## Rules

- Never force-push. Never delete a tag without explicit user instruction.
- If a step fails partway through (e.g. push rejected), stop and surface the error. Do not auto-revert the version bump commit.
- Respect the project style guide: no em dashes in commit messages or release notes.
- This repo's release triggers a downstream `notify-superproject-vendor-pin` workflow on `release.published`. Mention this to the user after the release is created so they can verify the dispatch ran.
