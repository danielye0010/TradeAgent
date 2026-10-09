# Release workflow

Use a focused preparation branch and a pull request into current `main`. Preserve
strategy identities, persisted schemas/protocols and operator state. Python metadata
uses PEP 440 (`1.0.0b1`); Git tags use semantic pre-release names
(`v1.0.0-beta.1`). Future public releases increment major for incompatible public
interfaces, minor for compatible features and patch for compatible fixes. A beta
tag remains a pre-release; the major number does not establish operational maturity.

1. Fetch current `main`; check that the proposed tag and GitHub release do not exist.
2. Update `pyproject.toml`, `CHANGELOG.md` and relevant installation examples.
   Keep release notes limited to features, installation, platforms and limitations.
3. Run the complete suite, Ruff lint/format, compile, project checks, build and Twine.
   Regenerate the legacy deployment manifest when its inputs change.
4. Run `python scripts/check_release.py --tag v1.0.0-beta.1 --dist dist`.
   It checks tracked source, packaged contents, version consistency and known
   credential/runtime patterns, then writes `dist/SHA256SUMS`. Inspect examples and
   configuration too; pattern scanning cannot detect every possible secret.
5. Install the wheel in a fresh Linux environment. From outside the checkout,
   verify import/version, `pip check`, CLI help, the synthetic demo/inspection,
   SHADOW validation and a simulated paper lifecycle.
6. Push the branch, open a PR and merge only after every CI job passes. Re-run CI
   on the resulting `main` commit and verify its exact SHA. The CI package job
   retains the validated wheel, source distribution and checksums as
   `dist-COMMIT_SHA`.
7. Create an annotated tag on that verified commit and push it without force.
   Wait for all tag CI jobs, including the tag/version check, to succeed.
8. Download the distribution artifact from the successful CI run on the exact
   tagged commit. Verify `sha256sum -c SHA256SUMS`, then publish a GitHub pre-release
   with those three assets and the prepared notes. Do not publish to PyPI.
9. Download the published assets again. Verify the release URL/pre-release status,
   annotated tag's peeled commit, package versions and byte-for-byte checksums.

Never move a published tag or replace its artifacts. Correct a release through a
new version. CI has read-only repository permissions and never executes LIVE,
uses owner configuration or installs a SHADOW timer. Publication remains an explicit
maintainer action; no separate deployment pipeline is required.
