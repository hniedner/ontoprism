import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import DataTableTestHost, { type TestRow } from './DataTable-fixture.svelte';

const payload = '<img src=x onerror=alert(1)><script>alert(2)</script><svg onload=alert(3)>';
const hostileRow: TestRow = {
	id: 'safe-id',
	name: payload,
	group: payload,
	rank: 1,
	active: true
};

function expectNoInjectedMarkup(container: HTMLElement): void {
	expect(container.querySelector('img, script, svg')).toBeNull();
	for (const element of container.querySelectorAll('*')) {
		for (const attribute of element.getAttributeNames()) {
			expect(attribute.toLowerCase()).not.toMatch(/^on/);
		}
	}
}

describe('DataTable XSS surfaces', () => {
	it('renders core labels and compiled cell values as text without injected markup', () => {
		const { container } = render(DataTableTestHost, {
			rows: [hostileRow],
			caption: payload,
			regionLabel: payload
		});
		expect(screen.getAllByText(payload).length).toBeGreaterThanOrEqual(3);
		expectNoInjectedMarkup(container);
	});

	it('renders empty text without swallowing render failures or interpreting HTML', () => {
		const empty = render(DataTableTestHost, { rows: [], emptyMessage: payload });
		expect(screen.getByText(payload)).toBeInTheDocument();
		expectNoInjectedMarkup(empty.container);
	});
});
