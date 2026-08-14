import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import RepositoryKindBadge from './RepositoryKindBadge.svelte';

describe('RepositoryKindBadge', () => {
	it.each([
		['local-certified-proxy', 'Local certified proxy'],
		['remote-live-service', 'Remote live service']
	] as const)('renders %s with a non-colour accessible name', (kind, label) => {
		render(RepositoryKindBadge, { kind });

		expect(screen.getByText(label)).toBeVisible();
	});
});
