import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import DataTableTestHost, { type TestRow } from './DataTable-fixture.svelte';

const row: TestRow = { id: 'one', name: 'One', group: 'Group', rank: 1, active: true };

describe('DataTable sticky configuration', () => {
	it('applies deterministic offsets and opaque stacking to configured headers and cells', () => {
		render(DataTableTestHost, { rows: [row], sticky: true });
		const headers = screen.getAllByRole('columnheader');
		const cells = Array.from(document.querySelectorAll('tbody td'));
		expect(headers[0]).toHaveClass('sticky', 'bg-card', 'z-30');
		expect(headers[0]).toHaveStyle({ left: '0px' });
		expect(cells[0]).toHaveClass('sticky', 'bg-card', 'z-20');
		expect(cells[0]).toHaveClass('border-b');
		expect(headers[0]).toHaveClass('border-b');
		expect(cells[0]).toHaveStyle({ left: '0px' });
		expect(headers[1]).not.toHaveClass('sticky');
		expect(cells[1]).not.toHaveClass('sticky');
	});

	it('keeps the complete header group sticky at top zero', () => {
		render(DataTableTestHost, { rows: [row], sticky: true });
		expect(document.querySelector('thead')).toHaveClass('sticky', 'top-0', 'bg-card');
	});
});
