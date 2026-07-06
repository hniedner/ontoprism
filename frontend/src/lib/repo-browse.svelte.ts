// Shared browse/search state for the repository pages (NCIt, caDSR). Encapsulates the
// list-vs-search mode toggle, pagination offset, and load/error lifecycle that the
// repo pages otherwise duplicate. A Svelte 5 rune module: state is exposed via getters
// so callers stay reactive (and `q` is settable for `bind:value`).

interface PageResult {
	total: number;
}

type PageOpts = { limit: number; offset: number };

export interface RepoBrowse<P extends PageResult> {
	q: string;
	readonly submitted: string;
	readonly mode: 'browse' | 'search';
	readonly offset: number;
	readonly result: P | null;
	readonly loading: boolean;
	readonly error: string | null;
	readonly limit: number;
	load(nextOffset: number, term: string): Promise<void>;
	search(): void;
	suggest(term: string): void;
}

export function createRepoBrowse<P extends PageResult>(
	searchFn: (q: string, opts: PageOpts) => Promise<P>,
	listFn: (opts: PageOpts) => Promise<P>,
	limit = 25
): RepoBrowse<P> {
	let q = $state('');
	let submitted = $state('');
	let mode = $state<'browse' | 'search'>('browse');
	let offset = $state(0);
	let result = $state<P | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	async function load(nextOffset: number, term: string): Promise<void> {
		loading = true;
		error = null;
		try {
			const trimmed = term.trim();
			result = trimmed
				? await searchFn(trimmed, { limit, offset: nextOffset })
				: await listFn({ limit, offset: nextOffset });
			mode = trimmed ? 'search' : 'browse';
			submitted = trimmed;
			offset = nextOffset;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
			result = null;
		} finally {
			loading = false;
		}
	}

	return {
		get q() {
			return q;
		},
		set q(value: string) {
			q = value;
		},
		get submitted() {
			return submitted;
		},
		get mode() {
			return mode;
		},
		get offset() {
			return offset;
		},
		get result() {
			return result;
		},
		get loading() {
			return loading;
		},
		get error() {
			return error;
		},
		limit,
		load,
		search() {
			load(0, q);
		},
		suggest(term: string) {
			q = term;
			load(0, term);
		}
	};
}
