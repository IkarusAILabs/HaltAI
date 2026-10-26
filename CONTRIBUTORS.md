# Contributors

Thank you to everyone who has helped build HaltAI. This list credits merged contributions; see [CONTRIBUTING.md](CONTRIBUTING.md) to add yours.

## Maintainers

- [Ikarus AI Labs](https://github.com/IkarusAILabs): project direction, roadmap and core implementation

## Community contributors

| Contributor | Contributions |
|---|---|
| [@chiragjangiir](https://github.com/chiragjangiir) | **`haltai check --json`** and documented exit codes, giving incident tooling machine-readable output (#13, closes #7) · **Python 3.14 in CI** (#14, closes #5) · **CONTRIBUTING.md**, the contributor guide (#15, closes #4) |
| [@jay23606](https://github.com/jay23606) | **More AWS privilege-escalation paths for Claim 3** (#12, closes #9). Added the policy-version, permissions-boundary and trust-policy escape routes, plus a new `existing_compute` escape kind for updating existing Lambda functions and ECS services under another role. This hardens the check that agents cannot undo their own containment. |

## How credit works

- Every merged pull request is credited here, under the author's GitHub handle, when it merges or at the next release.
- Release notes in `CHANGELOG.md` credit contributors by handle for the changes in that release.
- Reviews, bug reports, design discussion and drill feedback count too. Maintainers add them here on request or when they materially shape a release.
