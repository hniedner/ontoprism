/** Build breadcrumb trail from the current pathname. */
export interface Crumb {
	label: string;
	href: string;
}

const LABELS: Record<string, string> = {
	ncit: 'NCIt Browser',
	cadsr: 'caDSR CDEs',
	uberon: 'Uberon/CL Browser',
	icdo: 'ICD-O Datasets',
	refresh: 'Refresh'
};

const LAYOUT_ONLY_SEGMENTS = new Set(['repositories']);

export function buildBreadcrumbs(pathname: string): Crumb[] {
	if (pathname === '/repositories/icdo/4.0/topography/congruence') {
		return [
			{ label: 'Home', href: '/' },
			{ label: LABELS.icdo, href: '/repositories/icdo' },
			{ label: 'Congruence report', href: pathname }
		];
	}
	const segments = pathname.split('/').filter(Boolean);
	const crumbs: Crumb[] = [{ label: 'Home', href: '/' }];
	let href = '';
	for (const seg of segments) {
		href += `/${seg}`;
		if (LAYOUT_ONLY_SEGMENTS.has(seg)) continue;
		if (segments[1] === 'icdo' && /^\d+\.\d+$/.test(seg)) continue;
		// A concept code or CDE id (last dynamic segment) shows verbatim.
		const label = LABELS[seg] ?? decodeURIComponent(seg);
		crumbs.push({ label, href });
	}
	return crumbs;
}
