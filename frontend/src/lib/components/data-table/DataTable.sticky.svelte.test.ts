import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import DataTableTestHost, { type TestRow } from './DataTable-fixture.svelte';

const row: TestRow = { id: 'one', name: 'One', group: 'Group', rank: 1, active: true };

describe('DataTable sticky configuration', () => {
	it('applies deterministic offsets and opaque stacking to configured headers and cells', () => {
		render(DataTableTestHost, { rows: [row], sticky: true, operations: { kind: 'none' } });
		const headers = screen.getAllByRole('columnheader');
		const cells = screen.getAllByRole('cell');
		expect(headers[0]).toHaveClass('sticky', 'bg-card', 'z-30');
		expect(headers[0]).toHaveStyle({ left: '0px' });
		expect(headers[1]).toHaveStyle({ left: '120px' });
		expect(headers[2]).toHaveStyle({ right: '0px' });
		expect(cells[0]).toHaveClass('sticky', 'bg-card', 'z-20');
		expect(cells[0]).toHaveStyle({ left: '0px' });
		expect(cells[1]).toHaveStyle({ left: '120px' });
		expect(cells[2]).toHaveStyle({ right: '0px' });
		expect(headers[3]).toHaveStyle({ top: '0px' });
		expect(headers[3].getAttribute('style')).not.toMatch(/left|right/);
		expect(cells[3]).not.toHaveClass('sticky');
	});

	it('keeps every header sticky at the configured top offset', () => {
		render(DataTableTestHost, { rows: [row], sticky: true, operations: { kind: 'none' } });
		for (const header of screen.getAllByRole('columnheader')) {
			expect(header).toHaveClass('sticky', 'top-0');
		}
	});
});
