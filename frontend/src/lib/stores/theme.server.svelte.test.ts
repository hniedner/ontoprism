import { describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';

// Server path: `browser` is false at import, so the store defaults to dark without
// touching localStorage and skips registering its persistence effect.
vi.mock('$app/environment', () => ({ browser: false }));

import { theme } from './theme.svelte';

describe('theme store (server / non-browser)', () => {
	it('defaults to dark without a browser environment', () => {
		expect(theme.current).toBe('dark');
	});

	it('still toggles in-memory (no effect side effects run)', () => {
		theme.toggle();
		flushSync();
		expect(theme.current).toBe('light');
	});
});
