#!/usr/bin/env node
// Fallow static-analysis gate: dead code, import cycles, duplication, complexity —
// the cross-file discipline layer ESLint can't cover. `fallow audit` gates ONLY
// findings INTRODUCED vs the base (new-only); the existing backlog is excluded.
//
// Single source of truth for the pre-commit hook, the `fallow` npm script, and CI, so
// the three never drift. Two guards:
//   1. config-loaded guard — fail loudly if fallow silently fell back to built-in
//      defaults (a renamed/missing .fallowrc.jsonc analyses the wrong file set).
//   2. base resilience — if origin/main isn't resolvable (a shallow checkout with no
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

function resolveBase() {
	for (const ref of ['origin/main', 'main']) {
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
} catch (err) {
	if (err.code) {
		console.error('Cannot run fallow — run `npm ci` in frontend/ to install it.');
		process.exit(1);
	}
	throw err;
}

// Guard 2 — no base ⇒ skip (never block on environment).
const base = resolveBase();
if (!base) {
	console.warn('fallow: no base ref (origin/main) — skipping the new-only gate.');
	process.exit(0);
}

try {
	execFileSync('npx', ['fallow', 'audit', '--changed-since', base], { stdio: 'inherit' });
} catch (err) {
	process.exit(err.status ?? 1);
}
