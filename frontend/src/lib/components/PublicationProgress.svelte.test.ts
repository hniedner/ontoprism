import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import PublicationProgress from './PublicationProgress.svelte';

describe('PublicationProgress', () => {
	it('renders backend-computed D93 outcome and review-flag counts', () => {
		render(PublicationProgress, {
			progress: {
				run_id: 'published-run',
				publication_status: 'provisional',
				publication_notice: 'expert review, not an NCIt release',
				total_concepts: 20,
				outcome_counts: {
					decomposed: 12,
					residual: 2,
					'semantic-excluded': 3,
					'atomic-no-op': 2,
					unknown: 1
				},
				review_flag_counts: {
					'needs-review': 4,
					'unresolved-r101-loss': 1,
					'mint-filler': 3
				}
			}
		});

		expect(screen.getByText('expert review, not an NCIt release')).toBeInTheDocument();
		expect(screen.getByText('20')).toBeInTheDocument();
		expect(screen.getByText('decomposed').nextElementSibling).toHaveTextContent('12');
		expect(screen.getByText('needs-review').nextElementSibling).toHaveTextContent('4');
		expect(screen.queryByText('include', { exact: true })).not.toBeInTheDocument();
		expect(screen.queryByText('exclude', { exact: true })).not.toBeInTheDocument();
		expect(screen.queryByText('unresolved-visible', { exact: true })).not.toBeInTheDocument();
		expect(screen.queryByText(/oracle/i)).not.toBeInTheDocument();
	});
});
