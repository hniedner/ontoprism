/** Build breadcrumb trail from the current pathname. */
export interface Crumb {
	label: string;
	href: string;
}

const LABELS: Record<string, string> = {
	ncit: 'NCIt Browser',
	cadsr: 'caDSR CDEs',
	refresh: 'Refresh'
};

const LAYOUT_ONLY_SEGMENTS = new Set(['repositories']);

export function buildBreadcrumbs(pathname: string): Crumb[] {
	const segments = pathname.split('/').filter(Boolean);
	const crumbs: Crumb[] = [{ label: 'Home', href: '/' }];
	let href = '';
	for (const seg of segments) {
		href += `/${seg}`;
		if (LAYOUT_ONLY_SEGMENTS.has(seg)) continue;
		// A concept code or CDE id (last dynamic segment) shows verbatim.
		const label = LABELS[seg] ?? decodeURIComponent(seg);
		crumbs.push({ label, href });
	}
	return crumbs;
}
