// Small, pure, generic sorting helpers for result tables.

export type SortDir = 'asc' | 'desc';

/** Stable sort of `rows` by `key`, nulls last. Returns a new array. */
export function sortBy<T>(rows: readonly T[], key: (row: T) => string | number | null, dir: SortDir): T[] {
	const factor = dir === 'asc' ? 1 : -1;
	return [...rows].sort((a, b) => {
		const av = key(a);
		const bv = key(b);
		if (av === null && bv === null) return 0;
		if (av === null) return 1; // nulls always last, regardless of dir
		if (bv === null) return -1;
		if (av < bv) return -1 * factor;
		if (av > bv) return 1 * factor;
		return 0;
	});
}

/** Toggle a sort direction. */
export function nextDir(current: SortDir): SortDir {
	return current === 'asc' ? 'desc' : 'asc';
}
