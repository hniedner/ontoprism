import { describe, expect, it, vi } from 'vitest';

// Browser path with a previously-persisted choice: initialTheme must read it back.
vi.mock('$app/environment', () => ({ browser: true }));
vi.hoisted(() => {
	const store = new Map<string, string>([['ontoprism-theme', 'light']]);
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

describe('theme store (restores persisted choice)', () => {
	it('initializes from the stored value rather than the dark default', () => {
		expect(theme.current).toBe('light');
	});
});
