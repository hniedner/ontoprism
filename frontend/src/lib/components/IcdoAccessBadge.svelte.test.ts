import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import IcdoAccessBadge from './IcdoAccessBadge.svelte';

describe('IcdoAccessBadge', () => {
	it.each([
		['ready-and-entitled', 'Ready and entitled'],
		['entitlement-required', 'Entitlement required'],
		['unavailable', 'Unavailable']
	] as const)('renders %s as an explicit access state', (status, label) => {
		render(IcdoAccessBadge, { status });

		expect(screen.getByText(label)).toBeVisible();
	});
});
