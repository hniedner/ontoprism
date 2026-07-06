import { describe, expect, it, vi, beforeEach } from 'vitest';
import { flushSync } from 'svelte';

// The store reads `browser` and localStorage at import; force the browser path and
// provide an in-memory localStorage (jsdom's isn't guaranteed in this env) so the
// persistence + <html> class effect run. Hoisted so both exist before the import.
vi.mock('$app/environment', () => ({ browser: true }));
vi.hoisted(() => {
	const store = new Map<string, string>();
	globalThis.localStorage = {
		getItem: (k: string) => store.get(k) ?? null,
		setItem: (k: string, v: string) => void store.set(k, String(v)),
		removeItem: (k: string) => void store.delete(k),
		clear: () => store.clear(),
		key: () => null,
		get length() {
			return store.size;
		}
	} as Storage;
});

import { theme } from './theme.svelte';

beforeEach(() => {
	// Normalize to the default before each assertion (singleton shared across tests).
	if (theme.current !== 'dark') theme.toggle();
	flushSync();
});

describe('theme store', () => {
	it('defaults to dark', () => {
		expect(theme.current).toBe('dark');
	});

	it('toggles between dark and light', () => {
		theme.toggle();
		expect(theme.current).toBe('light');
		theme.toggle();
		expect(theme.current).toBe('dark');
	});

	it('reflects the choice on the <html> element and persists it', () => {
		theme.toggle(); // -> light
		flushSync();
		expect(document.documentElement.classList.contains('dark')).toBe(false);
		expect(localStorage.getItem('ontoprism-theme')).toBe('light');

		theme.toggle(); // -> dark
		flushSync();
		expect(document.documentElement.classList.contains('dark')).toBe(true);
		expect(localStorage.getItem('ontoprism-theme')).toBe('dark');
	});
});
