---
name: ontoprism-outside-in-tests
description: "Use when writing or planning new tests (TDD, a bug fix, a feature): state the behaviour first, test it where the caller meets it, keep only tests that state behaviour, and make every kept test deterministic. For pruning existing tests use ontoprism-test-audit."
---

# Outside-in tests

Built on an idea from imaurer's "Outside-in tests" gist (https://gist.github.com/imaurer/ac31f596bcfd7f46afe1c7dceedcba21),
which pairs with test-audit. The determinism section comes from our own CI failures.

TDD leaves a small test behind at every step. Unchecked, these pile up into **test sediment**: slower builds, breakage on
refactors that keep behaviour, and wrong behaviour locked in, so that a fix must first defend the old wrong answer. This
skill stops the pile from forming. test-audit removes a pile that already exists.

**Goal:** one set of tests that states the behaviour of the working software. Each test makes one claim no other test
makes, can fail for the reason its name gives, and gives the same answer on every machine, seed and order.

## 1. State the behaviour first
Before any code, write each behaviour as given / when / then, in the caller's words. For example: "given a user without
the phi role, when they open a PHI route, then they get 403 and an audit record". Put edge cases in one table: boundaries,
empty input, bad input, limits.

## 2. Test where the caller meets it
- Test at the entry point the user or caller uses: the CLI, the API route, the page. The layers underneath may change
  while the behaviour holds.
- Test an internal unit directly only when it owns behaviour worth stating alone, such as a parser. Its interface is
  then its outside.
- Each behaviour has one owning test. A bug gets one regression test where it shows, not one at every layer it
  crossed.

## 3. Red, green, remove
1. Write the outside-in test. Watch it fail **for the stated reason**, not with an import error or a fixture crash.
2. When a step is hard, drop to unit tests. They are scaffolding for work in progress.
3. Make the test pass.
4. Before landing, keep a test only if it is one of the four kinds below, and delete the scaffolding.

## 4. The four kinds that stay
- **Behaviour tests** through the real entry point.
- **Edge-case tables.**
- **Contract checks** on what others depend on: schemas, output formats, security and privacy rules, audit records,
  release and deploy artefacts.
- **Regression tests** that failed on the code before the fix.

## 5. Gate before adding a test
A missing answer means: do not add it.
1. What behaviour does it protect?
2. What credible mistake makes it fail?
3. Why does no existing test catch that?
4. Does it need a hook, export or flag that only tests use? Then move it to a real boundary.

## 6. Make it deterministic
A test that passes locally and fails on CI is a defect in the test or in the app. Never pass a rerun off as a fix.
- **Time:** never assert a wall-clock bound on a shared or parallel runner. Count the work (queries, calls, rows), or
  run the timing under a benchmark or performance marker. The test controls ages and clocks itself, through an injected
  clock or a frozen time, instead of waiting on real time. Never compute time-dependent data at import or collection
  time; build it when the test runs.
- **Waits:** wait for an outcome (an element state, a response, an event), never for a fixed delay. A debounced or
  scheduled side effect the setup triggers must be awaited or stubbed.
- **State:** every process-global the test touches (singletons, caches, registries, logging handlers and filters, open
  files and connections, env vars) is restored or reset when the test ends. Close resources explicitly; don't leave it
  to garbage collection.
- **Order:** the test passes alone, in any order and under any worker split. A CI red is reproduced with CI's seed and
  worker layout before anyone claims a cause.
- **Environment:** the test does not inherit settings from a local `.env` or the developer's machine. Run it under CI
  settings before pushing.
- **New guards:** a change that makes code refuse, raise or fail ships report-only first. Measure its hit count across
  every suite and several seeds, and switch it to refuse only at zero unintended hits.

## 7. Signs of sediment
- A test asserts what its own setup made true.
- One claim is tested at several layers.
- A refactor that keeps behaviour breaks many tests.
- A fix needs test edits that defend the old wrong answer.
- A test is named for a ticket or a step instead of a behaviour.

## 8. Clean-up rules
- Coverage counts lines run, not claims checked. Use it as a map of what is untested, not as a target.
- Delete with evidence, not by category. Mutation testing shows which tests catch what nothing else catches.
- Never delete the only test of a behaviour: write the outside-in test first, then delete.
- Slow or old is no reason to delete. Redundant or tautological is.
- A kept test that fails may have found a product bug. Fix the code, not the test.
- Remove in small batches, run the suite, land. Don't let cleanup become its own proof spiral.
