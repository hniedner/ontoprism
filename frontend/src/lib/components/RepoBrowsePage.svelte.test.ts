import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import CursorPagination from './CursorPagination.svelte';
import RepoBrowsePage from './RepoBrowsePage.svelte';
import RepoBrowsePageIntentFixture from './RepoBrowsePage-intent-fixture.svelte';

const goto = vi.fn().mockResolvedValue(undefined);
vi.mock('$app/navigation', () => ({ goto: (target: string) => goto(target) }));
vi.mock('$app/paths', () => ({ resolve: (target: string) => target }));
const appState = vi.hoisted(() => ({
	page: { url: new URL('https://example.test/repositories/ncit') },
	navigating: { to: null }
}));
vi.mock('$app/state', () => appState);

interface Hit {
	id: string;
}

const helpText = createRawSnippet(() => ({
	render: () => `<span data-testid="help">help copy</span>`
}));
const instruction = createRawSnippet(() => ({ render: () => '<p>Enter a remote query</p>' }));
const results = createRawSnippet<[Hit[], unknown, string]>((getHits, _getOperations, getEmptyMessage) => ({
	render: () => `<div data-testid="results">${getHits().length} rows${getHits().length ? '' : `: ${getEmptyMessage()}`}</div>`
}));

function setup(query = '', offset = 0, total = 42, route = '/repositories/ncit', filters: Record<string, string[]> = {}, navigationTotal?: number) {
	return render(RepoBrowsePage, {
		title: 'NCIt Browser',
		description: 'Browse concepts',
		route: route as '/repositories/ncit',
		helpText,
		placeholder: 'Search…',
		ariaLabel: 'Search NCIt',
		suggestions: ['melanoma'],
		browseTitle: 'All concepts',
		countLabel: (count: number, mode: string) => `${count} (${mode})`,
		results: results as never,
		initial: { result: { total, hits: total === 0 ? [] : [{ id: 'a' }] }, query, offset, size: 25, sort: 'source', filters },
		defaultSort: 'source',
		sortKeys: {},
		filterKeys: Object.fromEntries(Object.keys(filters).map((key) => [key, key])),
		navigationTotal
	});
}

describe('RepoBrowsePage', () => {
	beforeEach(() => {
		goto.mockClear();
		appState.page.url = new URL('https://example.test/repositories/ncit');
	});

	it('renders server-loaded browse data and a progressively functional GET form', () => {
		setup();

		expect(screen.getByRole('heading', { name: 'NCIt Browser' })).toBeInTheDocument();
		expect(screen.getByText('All concepts')).toBeInTheDocument();
		expect(screen.getByText('42 (browse)')).toBeInTheDocument();
		expect(screen.getByTestId('results')).toHaveTextContent('1 rows');
		const searchbox = screen.getByRole('searchbox');
		expect(searchbox).toHaveAttribute('name', 'q');
		expect(searchbox.closest('form')).toHaveAttribute('method', 'get');
	});

	it('renders server-loaded search and pagination state', () => {
		setup('melanoma', 25, 100);

		expect(screen.getByRole('searchbox')).toHaveValue('melanoma');
		expect(screen.getByText('Results for “melanoma”')).toBeInTheDocument();
		expect(screen.getByText('100 (search)')).toBeInTheDocument();
		expect(screen.getByText('Page 2 of 4')).toBeInTheDocument();
		expect(screen.getAllByRole('navigation', { name: 'Pagination' })).toHaveLength(1);
	});

	it('keeps offset navigation within an upstream result window while reporting the full total', () => {
		setup('melanoma', 9975, 25_000, '/repositories/pubmed', {}, 10_000);
		expect(screen.getByText('Page 400 of 400')).toBeInTheDocument();
		expect(within(screen.getByRole('navigation', { name: 'Pagination' })).getByText('25,000')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled();
	});

	it('enhances search and pagination as URL navigation', async () => {
		setup('', 0, 100);
		await fireEvent.input(screen.getByRole('searchbox'), { target: { value: 'melanoma' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Search' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/ncit?q=melanoma');

		await fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/ncit?offset=25');
	});

	it('uses suggestion chips to update the URL query', async () => {
		setup();
		await fireEvent.click(screen.getByRole('button', { name: 'melanoma' }));
		expect(goto).toHaveBeenCalledWith('/repositories/ncit?q=melanoma');
	});

	it('navigates within the Uberon repository route', async () => {
		setup('', 0, 42, '/repositories/uberon');
		await fireEvent.input(screen.getByRole('searchbox'), { target: { value: 'lung' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Search' }));
		expect(goto).toHaveBeenCalledWith('/repositories/uberon?q=lung');
	});

	it('distinguishes source-empty browse state from no matches while retaining results', () => {
		const browse = setup('', 0, 0);
		expect(screen.getByTestId('results')).toHaveTextContent('0 rows: This repository contains no records.');
		browse.unmount();

		setup('missing', 0, 0);
		expect(screen.getByTestId('results')).toHaveTextContent('0 rows: No records matched the current query and filters.');
	});

	it('keeps populated categorical filters recoverable when they match no rows', async () => {
		setup('', 0, 0, '/repositories/ncit', { representation_status: ['legacy-precoordinated'] });

		expect(screen.getByTestId('results')).toHaveTextContent('0 rows: No records matched the current query and filters.');
	});

	it('maps table column filter intents to declared URL filter keys', async () => {
		render(RepoBrowsePageIntentFixture, {
			filterKeys: { statusColumn: 'representation_status' },
			initialFilters: { representation_status: [] },
			intent: { kind: 'filter', columnId: 'statusColumn', filter: { kind: 'categorical', selected: ['legacy-precoordinated'] } }
		});

		await fireEvent.click(screen.getByRole('button', { name: 'Send table intent' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/ncit?representation_status=legacy-precoordinated');
	});

	it('preserves combined text and category intents and clears both from the URL', async () => {
		render(RepoBrowsePageIntentFixture, {
			filterKeys: { status: 'representation_status' }, textKeys: { status: 'representation_status', name: 'label' },
			initialFilters: { representation_status: ['legacy-precoordinated'] }, initialTextFilters: { label: 'neo' },
			intent: { kind: 'filter', columnId: 'status', filter: { kind: 'categorical', selected: ['legacy-precoordinated'], text: 'Legacy' } }
		});
		await fireEvent.click(screen.getByRole('button', { name: 'Send table intent' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/ncit?representation_status=legacy-precoordinated&text_representation_status=Legacy');
	});

	it('maps text-only column filtering to the URL and clears it without changing the search', async () => {
		const rendered = render(RepoBrowsePageIntentFixture, {
			filterKeys: {}, textKeys: { name: 'label' }, initialFilters: {},
			intent: { kind: 'filter', columnId: 'name', filter: { kind: 'text', text: 'melanoma' } }
		});
		await fireEvent.click(screen.getByRole('button', { name: 'Send table intent' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/ncit?text_label=melanoma');
		rendered.unmount();
		goto.mockClear();
		render(RepoBrowsePageIntentFixture, {
			filterKeys: {}, textKeys: { name: 'label' }, initialFilters: {}, initialTextFilters: { label: 'melanoma' },
			intent: { kind: 'clear-filter', columnId: 'name' }
		});
		await fireEvent.click(screen.getByRole('button', { name: 'Send table intent' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/ncit');
	});

	it('fails visibly instead of navigating for an unmapped table filter intent', async () => {
		render(RepoBrowsePageIntentFixture, {
			filterKeys: { status: 'representation_status' },
			initialFilters: { representation_status: [] },
			intent: { kind: 'clear-filter', columnId: 'unknown' }
		});

		await fireEvent.click(screen.getByRole('button', { name: 'Send table intent' }));
		expect(screen.getByRole('alert')).toHaveTextContent('No server filter mapping for unknown');
		expect(goto).not.toHaveBeenCalled();
	});

	it('fails visibly instead of rejecting navigation for an unmapped sort intent', async () => {
		render(RepoBrowsePageIntentFixture, {
			filterKeys: {},
			initialFilters: {},
			sortKeys: { name: { asc: 'name:asc', desc: 'name:desc' } },
			intent: { kind: 'sort', sort: { key: 'unknown', direction: 'asc' } }
		});

		await fireEvent.click(screen.getByRole('button', { name: 'Send table intent' }));
		expect(screen.getByRole('alert')).toHaveTextContent('No server sort mapping for unknown:asc');
		expect(goto).not.toHaveBeenCalled();
	});

	it('hosts remote instruction and typed 429 states without rendering a false empty result', () => {
		const common = {
			title: 'PubMed', description: 'Literature', route: '/repositories/pubmed', helpText,
			placeholder: 'Search…', ariaLabel: 'Search PubMed', suggestions: [], browseTitle: 'Articles',
			countLabel: (count: number) => `${count} articles`, results: results as never,
			initial: { result: { total: 0, hits: [] }, query: '', offset: 0, size: 25, sort: 'relevance', filters: {} },
			defaultSort: 'relevance', sortKeys: {}, filterKeys: {}, instruction
		};
		const empty = render(RepoBrowsePage, {
			...common,
			remote: { service: 'NCBI PubMed', state: 'empty', error: null }
		} as never);
		expect(screen.getByText('Remote live service')).toBeVisible();
		expect(screen.getByText('Enter a remote query')).toBeVisible();
		expect(screen.queryByTestId('results')).not.toBeInTheDocument();
		empty.unmount();

		render(RepoBrowsePage, {
			...common,
			remote: {
				service: 'NCBI PubMed',
				state: 'error',
				error: { remoteState: 'rate-limited', message: 'PubMed rate limit reached.' }
			}
		} as never);
		expect(screen.getByRole('alert')).toHaveAttribute('data-remote-state', 'rate-limited');
	});

	it('owns cursor navigation for a remote page', async () => {
		appState.page.url = new URL('https://example.test/repositories/clinicaltrials?q=melanoma&cursor=opaque');
		render(RepoBrowsePage, {
			title: 'ClinicalTrials.gov', description: 'Trials', route: '/repositories/clinicaltrials', helpText,
			placeholder: 'Search…', ariaLabel: 'Search trials', suggestions: [], browseTitle: 'Trials',
			countLabel: (count: number) => `${count} trials`, results: results as never,
			initial: { result: { total: 42, hits: [{ id: 'trial' }] }, query: 'melanoma', offset: 0, size: 25, sort: 'relevance', filters: {} },
			defaultSort: 'relevance', sortKeys: {}, filterKeys: {}, instruction,
			remote: { service: 'ClinicalTrials.gov', state: 'ready', error: null },
			cursor: { trail: ['opaque'], next: 'next' }
		} as never);

		await fireEvent.click(screen.getByRole('button', { name: 'Previous page' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/clinicaltrials?q=melanoma');
		await fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/clinicaltrials?q=melanoma&cursor=opaque&cursor=next');
	});
});

describe('CursorPagination', () => {
	it('offers truthful previous and next cursor navigation without page numbers', async () => {
		const onPrevious = vi.fn();
		const onNext = vi.fn();
		render(CursorPagination, {
			count: 25,
			total: 80,
			hasPrevious: true,
			hasNext: true,
			size: 25,
			onPrevious,
			onNext,
			onSize: vi.fn()
		});
		await fireEvent.click(screen.getByRole('button', { name: 'Previous page' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
		expect(onPrevious).toHaveBeenCalledOnce();
		expect(onNext).toHaveBeenCalledOnce();
		expect(screen.queryByText(/Page \d/)).not.toBeInTheDocument();
	});
});
