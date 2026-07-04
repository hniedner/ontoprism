/**
 * Theme store — light/dark mode with persistence.
 *
 * Applies the `.dark` class to <html> (Tailwind v4 custom variant) and
 * persists the choice to localStorage. Defaults to dark.
 */
import { browser } from '$app/environment';

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'ontoprism-theme';

function initialTheme(): Theme {
	if (!browser) return 'dark';
	const stored = localStorage.getItem(STORAGE_KEY);
	if (stored === 'light' || stored === 'dark') return stored;
	return 'dark';
}

class ThemeStore {
	current = $state<Theme>(initialTheme());

	constructor() {
		if (browser) {
			$effect.root(() => {
				$effect(() => {
					document.documentElement.classList.toggle('dark', this.current === 'dark');
					localStorage.setItem(STORAGE_KEY, this.current);
				});
			});
		}
	}

	toggle(): void {
		this.current = this.current === 'dark' ? 'light' : 'dark';
	}
}

export const theme = new ThemeStore();
