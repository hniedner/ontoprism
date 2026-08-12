export function parseOffset(raw: string | null): number {
	if (!raw || !/^\d+$/.test(raw)) return 0;
	const value = Number(raw);
	return Number.isSafeInteger(value) ? value : 0;
}
