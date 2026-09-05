export function repositoryGridHref(
	route: string,
	current: URL,
	update: (params: URLSearchParams) => void
): string {
	const params = new URLSearchParams(current.search);
	update(params);
	return `${route}${params.size ? `?${params}` : ''}`;
}
