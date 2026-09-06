function repositoryGridHref(
	route: string,
	current: URL,
	update: (params: URLSearchParams) => void
): string {
	const params = new URLSearchParams(current.search);
	update(params);
	return `${route}${params.size ? `?${params}` : ''}`;
}

export function clearGridFilters(params: URLSearchParams, filterKeys: readonly string[]): void {
	for (const key of filterKeys) params.delete(key);
}

export function navigateRepositoryGrid(
	route: string,
	current: URL,
	update: (params: URLSearchParams) => void,
	navigate: (target: string) => void
): void {
	navigate(repositoryGridHref(route, current, update));
}
