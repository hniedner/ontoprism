import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import RemoteSearchState from './RemoteSearchState.svelte';

describe('RemoteSearchState', () => {
	it.each(['unavailable', 'timeout', 'rate-limited'] as const)(
		'renders the typed %s state and sanitized message',
		(state) => {
			render(RemoteSearchState, {
				service: 'PubMed',
				state,
				message: 'Safe service message.'
			});

			expect(screen.getByRole('alert')).toHaveAttribute('data-remote-state', state);
			expect(screen.getByText('Safe service message.')).toBeVisible();
		}
	);
});
