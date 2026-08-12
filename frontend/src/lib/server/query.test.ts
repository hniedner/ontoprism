import { describe, expect, it } from 'vitest';
import { parseOffset } from './query';

describe('parseOffset', () => {
	it.each([
		[null, 0],
		['', 0],
		['0', 0],
		['25', 25],
		['-1', 0],
		['1junk', 0],
		['1.5', 0],
		['9007199254740992', 0]
	])('maps %j to %i', (raw, expected) => {
		expect(parseOffset(raw)).toBe(expected);
	});
});
