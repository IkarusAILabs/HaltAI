# Contributing to HaltAI

Thank you for your interest in contributing to HaltAI.

HaltAI provides containability assurance for registered AI-agent deployments. This guide explains how to set up your environment, run the required quality checks, and submit contributions.

---

## Development Setup

HaltAI requires Python 3.11+. Install dependencies and editable development packages:

```bash
git clone https://github.com/IkarusAILabs/HaltAI.git
cd HaltAI
pip install -e ".[dev]"
```

---

## Simulated by Default

- **No AWS account required:** All core development and static checks use the `simulated` backend. You do not need AWS credentials or access to contribute.
- **Never test against production:** The real AWS backend is disruptive and modifies IAM and gateway policies. Do not switch to `backend: aws` against production deployments.

---

## Verification Checks

CI runs four mandatory checks on every pull request. Run them locally before submitting:

```bash
# 1. Lint and style checks
ruff check .

# 2. Type checking
mypy src

# 3. Test suite
pytest

# 4. Scenario matrix verification
cd examples && haltai matrix
```

---

## Core Project Rules

1. **Honest evidence accounting:** Missing evidence must always be reported as an `AssessmentLimitation` or `ClaimResult.UNKNOWN`, never silently treated as a pass. A change that could turn a failure into `verified` must include tests proving it cannot produce false positives.
2. **Adding simulated scenarios:**
   - Define the new scenario in `src/haltai/scenarios.py` with its expected outcome across claims.
   - Implement the simulated backend behavior in `src/haltai/backends/simulated.py`.
   - Update `README.md` if the scenario is user-facing or introduces a new failure class.
   - Add a row for the scenario to the scoring table in `docs/baseline-checklist.md`, rating both the checklist and HaltAI.
3. **Pull request conventions:**
   - **Focused scope:** One change or bug fix per pull request.
   - **Regression tests:** Include automated tests for any behavior changes or bug fixes.
   - **Documentation:** Keep documentation, `--help` strings, and examples updated alongside code changes.

---

## Credit

Every merged contribution is credited by GitHub handle in [CONTRIBUTORS.md](CONTRIBUTORS.md) and in the release notes for the release it ships in. Reviews, bug reports, design input and drill feedback count too.

---

## License

HaltAI is licensed under Apache-2.0. By contributing, you agree that your contributions are licensed under the same terms (see [LICENSE](LICENSE)).
