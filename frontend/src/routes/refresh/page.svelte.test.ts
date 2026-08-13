import { fireEvent, render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { refreshRepositories } from '$lib/api';
import Page from './+page.svelte';

vi.mock('$lib/api', () => ({ refreshRepositories: vi.fn() }));

const refreshMock = vi.mocked(refreshRepositories);

describe('repository refresh metadata', () => {
	beforeEach(() => refreshMock.mockClear());

	it('renders certified identities and typed unhealthy reasons', async () => {
		refreshMock.mockResolvedValue({
			refreshed_at: '2026-08-10T20:00:00+00:00',
			repositories: [
				{
					state: 'ready',
					repository: 'ncit',
					source_identity: 'a'.repeat(64),
					manifest_identity: 'b'.repeat(64),
					release: '26.07d',
					activated_at: '2026-08-10T19:30:00+00:00',
					observation: {
						default_triples: 12_980_813,
						stated_triples: 10_855_010,
						named_graphs: [],
						default_version: '26.07d',
						stated_version: '26.07d',
						restriction_count: 150_000,
						has_required_restriction: true,
						default_has_stated_only_sentinel: false,
						stated_has_stated_only_sentinel: true
					}
				},
				{
					state: 'unhealthy',
					repository: 'cadsr',
					reason: 'manifest-missing',
					message: 'source provenance is absent'
				},
				{
					state: 'ready', repository: 'icdo', edition: '3.2', axis: 'morphology',
					source_identity: 'c'.repeat(64), serving_identity: 'd'.repeat(64),
					activation_identity: 'e'.repeat(64), row_count: 1143,
					activated_at: '2026-08-10T19:30:00+00:00'
				}
			]
		});
		render(Page);

		await fireEvent.click(screen.getByRole('button', { name: 'Refresh repositories' }));

		expect(await screen.findByText('26.07d')).toBeInTheDocument();
		expect(screen.getByText('a'.repeat(64))).toBeInTheDocument();
		expect(screen.getByText('manifest-missing')).toBeInTheDocument();
		expect(screen.getByText('source provenance is absent')).toBeInTheDocument();
		expect(screen.getByText('3.2 morphology (1,143)')).toBeInTheDocument();
	});
});
