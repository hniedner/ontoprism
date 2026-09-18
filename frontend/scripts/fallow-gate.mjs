#!/usr/bin/env node
// Fallow static-analysis gate: dead code, import cycles, duplication, complexity —
// the cross-file discipline layer ESLint can't cover. `fallow audit` gates ONLY
// findings INTRODUCED vs the base (new-only); the existing backlog is excluded.
//
// Single source of truth for the pre-commit hook, the `fallow` npm script, and CI, so
// the three never drift. Two guards:
//   1. config-loaded guard — fail loudly if fallow silently fell back to built-in
//      defaults (a renamed/missing .fallowrc.jsonc analyses the wrong file set).
//   2. base resilience — if no base ref is resolvable (a shallow checkout with no
//      base), warn and pass rather than block; fallow is a discipline layer, not a
//      hard dependency. CI uses fetch-depth: 0 so the gate is actually enforced there.
import { execFileSync } from 'node:child_process';

function tryGit(args) {
	try {
		return execFileSync('git', args, { encoding: 'utf8' }).trim();
	} catch {
		return '';
	}
}

// The base is the branch the PR targets: GITHUB_BASE_REF in a pull-request job, or
// FALLOW_BASE locally (set it to the milestone branch on an issue branch). If neither
// is set, or the named ref is not fetched, origin/main; the chosen base is printed. Findings between origin/main and a milestone branch belong to
// the issue PRs already merged there, not to the one under review.
function resolveBase() {
	const candidates = [];
	for (const name of [process.env.GITHUB_BASE_REF, process.env.FALLOW_BASE]) {
		if (name) candidates.push(`origin/${name}`, name);
	}
	candidates.push('origin/main', 'main');
	for (const ref of candidates) {
		if (tryGit(['rev-parse', '--verify', '--quiet', ref])) return ref;
	}
	return '';
}

// Guard 1 — config must be our file, not fallow's silent defaults.
try {
	const config = execFileSync('npx', ['fallow', 'config'], { encoding: 'utf8' });
	if (!config.includes('.fallowrc.jsonc')) {
		console.error('fallow is not using frontend/.fallowrc.jsonc (silent default fallback).');
		process.exit(1);
	}
} catch {
	// fallow not installed (npx exits non-zero) or npx missing (spawn ENOENT) — either
	// way the fix is the same, so surface the guidance rather than a raw stack trace.
	console.error('Cannot run fallow — run `npm ci` in frontend/ to install it.');
	process.exit(1);
}

// Guard 2 — no base ⇒ skip (never block on environment).
const base = resolveBase();
if (!base) {
	console.warn('fallow: no base ref — skipping the new-only gate.');
	process.exit(0);
}

console.log(`fallow: new-only gate against ${base}`);
try {
	execFileSync('npx', ['fallow', 'audit', '--changed-since', base], { stdio: 'inherit' });
} catch (err) {
	process.exit(err.status ?? 1);
}
