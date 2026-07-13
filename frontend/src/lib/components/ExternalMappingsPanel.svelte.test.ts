import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import ExternalMappingsPanel from './ExternalMappingsPanel.svelte';

vi.mock('$lib/api', () => ({ getMappings: vi.fn() }));
import { getMappings } from '$lib/api';

const mock = vi.mocked(getMappings);

describe('ExternalMappingsPanel', () => {
	it('requests the mappings for the concept code', async () => {
		mock.mockResolvedValue({ code: 'C12400', mappings: [] });
		render(ExternalMappingsPanel, { code: 'C12400' });
		await screen.findByText('No upstream mappings.');
		expect(mock).toHaveBeenCalledWith('C12400');
	});

	it('shows an error message when the fetch fails', async () => {
		mock.mockRejectedValue(new Error('network error'));
		render(ExternalMappingsPanel, { code: 'C12400' });
		expect(await screen.findByText(/Failed to load/)).toBeInTheDocument();
	});

	it('renders mapping entries with badge and confidence', async () => {
		mock.mockResolvedValue({
			code: 'C12400',
			mappings: [
				{
					object_id: 'UBERON:0002046',
					predicate: 'http://www.w3.org/2004/02/skos/core#exactMatch',
					lifecycle: 'validated',
					confidence: 0.95,
					is_identity: true
				},
				{
					object_id: 'UBERON:0002048',
					predicate: 'http://www.w3.org/2004/02/skos/core#closeMatch',
					lifecycle: 'proposed',
					confidence: 0.7,
					is_identity: false
				}
			]
		});
		render(ExternalMappingsPanel, { code: 'C12400' });
		expect(await screen.findByText('UBERON:0002046')).toBeInTheDocument();
		expect(screen.getByText('95%')).toBeInTheDocument();
		expect(screen.getByText('core#exact')).toBeInTheDocument();
		expect(screen.getByText('identity')).toBeInTheDocument();
	});
});
