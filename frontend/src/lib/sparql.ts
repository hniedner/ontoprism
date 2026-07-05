// Pure helpers for rendering guarded-SPARQL results (unit tested).

import type { SparqlResultDoc } from './types';

export interface SparqlTable {
	/** Projected variable names, in SELECT order. */
	columns: string[];
	/** One row per binding; cell is the value or '' when the var is unbound. */
	rows: string[][];
	/** Set for ASK queries (which return a boolean, not bindings). */
	boolean?: boolean;
}

/** Flatten a SPARQL-JSON document into columns + rows for a result table. */
export function flattenSparql(doc: SparqlResultDoc): SparqlTable {
	if (typeof doc.boolean === 'boolean') {
		return { columns: ['result'], rows: [[String(doc.boolean)]], boolean: doc.boolean };
	}
	const columns = doc.head?.vars ?? [];
	const bindings = doc.results?.bindings ?? [];
	const rows = bindings.map((binding) => columns.map((col) => binding[col]?.value ?? ''));
	return { columns, rows };
}
