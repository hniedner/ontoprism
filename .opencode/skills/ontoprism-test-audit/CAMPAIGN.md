# Test-pruning campaign

Adapted from OpenClaw (MIT, see LICENSE.md). The examples come from OpenClaw's Telegram campaign.

Campaign mode prunes the whole test surface of one subsystem in one PR. The value bar, retention bar, candidate evidence
and validation in [SKILL.md](SKILL.md) apply to every lane. Each step ends on its completion criterion; do not start
the next step early.

## 1. Baseline
Pin a `main` SHA. Record the subsystem's test and support line counts and every test file's pass/fail state at that
SHA. Keep the baseline failures in a list of their own. In the Telegram campaign, all three were real delivery bugs, not
stale tests.
**Done when** every in-scope test file has a recorded baseline result.

## 2. Lanes and inventory
Split the surface into lanes along production owner boundaries, not along file prefixes. Include the subsystem's cases at
shared core boundaries, and its QA and live-proof harness tests.
**Done when** every test file and QA scenario the subsystem owns belongs to exactly one lane.

## 3. Read-only ledger per lane
Give each lane to its own read-only agent. The agent reads:
- every assigned test in full, including its parameter tables;
- the production owners, with their entry points, callers, history and CI routing.

Each test declaration goes into a written ledger with one mark. A parametrised test is one declaration unless its rows
need different marks.

The marks:
- `R` (retain): name the contract and the bug it catches.
- `F` (fix): retain the contract but repair the assertion, for example a vacuous negative.
- `C` (consolidate): name first the owner that absorbs the assertion.
- `D` (delete): name the proof that remains, or say why no contract exists.

Judge a test by its assertions, not its name.
**Done when** every declaration in the lane has a mark and an evidence line.

## 4. Layer plan per lane
The ledger is input, not the edit list. A second read-only pass starts from it and looks for the redundant layer, for
example several suites replaying one shared helper through one mocked collaborator around stronger real-boundary
suites. Name the keeper suite for each contract. Prefer the real transport boundary with a fake network over a mocked
collaborator. Fix any ledger errors this pass finds.
**Done when** each lane plan names:
- its retired files;
- the keeper for each contract;
- the assertions to carry into the keepers;
- the test-only production seams the change unlocks.

## 5. Cutover
Edit lane by lane, and route changes to shared harnesses through one owner. With each lane, remove the test-only seams it
unlocks. Register moved suites in CI routing. Write durable test-ownership rules into the project's instructions, drawn
only from mistakes this campaign actually found.
**Done when** every lane plan is applied and each lane's keepers pass.

## 6. Preservation review
Independent reviewers compare the deleted coverage against the keepers, one reviewer per boundary group. They look for:
- contracts that lost their only proof;
- new assertions that cannot fail.

For each restored contract, make one deliberate mutation of the production owner, confirm the keeper goes red, then
restore the source byte for byte.
**Done when** each reported gap is either restored, or rejected with source evidence, and every restored contract has a
caught mutation.

## 7. Product defects
A baseline failure that survives into a keeper is a bug report. Fix it at its owner in a separate commit. Prove the fix
through the real user flow, with a control run that reverts the fix and shows the old behaviour. Unrelated
discrepancies become follow-ups.
**Done when** each repaired defect has a failing control and a passing candidate on the same harness.

## 8. Reconcile and hand off
For a long campaign, merge `main` rather than rebasing. When `main` modified a file the campaign deleted, keep the
deletion and port the new contract into the keeper. Rerun the whole subsystem suite on the merged head.

Hand off with the SKILL.md report, plus:
- baseline and final line counts, with test and production lines counted separately;
- lanes, retired layers and keepers;
- the preservation gaps found, with their mutations;
- the product defects, with control and candidate proof.
