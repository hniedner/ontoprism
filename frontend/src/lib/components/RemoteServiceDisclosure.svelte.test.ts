import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import RemoteServiceDisclosure from './RemoteServiceDisclosure.svelte';

describe('RemoteServiceDisclosure', () => {
	it.each(['NCBI PubMed', 'ClinicalTrials.gov'] as const)(
		'renders persistent live-query, reproducibility, and privacy disclosure for %s',
		(service) => {
			render(RemoteServiceDisclosure, { service });

			const disclosure = screen.getByRole('note');
			expect(disclosure).toHaveTextContent(`${service} is queried live`);
			expect(disclosure).toHaveTextContent('not reproducible from certified local state');
			expect(disclosure).toHaveTextContent(`Query and request data are sent to ${service}`);
		}
	);
});
