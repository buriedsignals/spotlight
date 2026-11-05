import assert from 'node:assert/strict';
import test from 'node:test';
import { missingValues } from './claim-values.ts';

test('a claim\'s numbers and dates are found in its passage across formats, and a different value is named', () => {
	for (const [claim, passage] of [
		['paid 1.2M', 'paid 1,200,000'],
		['paid 1,200,000', 'paid 1.2 million'],
		['paid $1.2bn', 'paid 1 200 000 000 dollars'],
		['paid 1.200.000 euros', 'paid EUR 1,200,000'],
		['the rate was 6.4%', 'the rate stood at 6,4 percent'],
		['1,328 persons', '1328 persons'],
		['on 3 March 2024', 'dated 2024-03-03'],
		['on March 3rd, 2024', 'dated 03/03/2024'],
		['in October 2026', 'published 1 October 2026'],
		['in Sept. 2025', 'September 2025 report'],
		['in 2024', 'dated 2024-03-03'],
		['three times, 3 in all', 'three times'],
		['form 990 of 501(c)(3) bodies', 'form 990, a 501(c)(3)'],
		['Chair since FY2025', 'no year here'],
	]) assert.deepEqual(missingValues(claim, passage), [], `${claim} in ${passage}`);
	for (const [claim, passage, missing] of [
		['the rate was 7.7%', 'the rate was 6.5%', ['7.7']],
		['13,280 persons', '1,328 persons', ['13,280']],
		['paid 1.2M', 'paid 1,234,567', ['1.2M']],
		['on 4 March 2024', 'dated 2024-03-03', ['4 March 2024']],
		['in November 2026', 'on 1 October 2026', ['November 2026']],
		['since 2019', 'since 2018', ['2019']],
	] as const) assert.deepEqual(missingValues(claim, passage), missing, claim);
	assert.deepEqual(missingValues('rate 6.4% in August 2026', 'the rate was 6.4%', 'Euro indicators, August 2026 release'), [], 'a date may be elsewhere on the page');
	assert.deepEqual(missingValues('since 2019', 'the director', 'Annual report 2019'), [], 'a year too');
	assert.deepEqual(missingValues('rate 6.9%', 'the rate was 6.4%', 'a table with 6.9 in another row'), ['6.9%'.replace('%', '')], 'a figure must be in the passage');
	assert.deepEqual(missingValues('on 2024/03/03', 'dated 2024-03-03'), []);
	assert.deepEqual(missingValues('opened in 1885', 'opened in Basel', 'History: opened in 1885'), []);
	assert.deepEqual(missingValues('paid 1,000,000,000,001', 'paid 1,000,000,000,002'), ['1,000,000,000,001']);
	assert.deepEqual(missingValues('The Form 990 lists 12 directors', 'lists 12 directors'), [], 'a form\'s name is not a value');
	assert.deepEqual(missingValues('They form 12 groups', 'They form 13 groups'), ['12'], 'the verb keeps its number');
	assert.deepEqual(missingValues('Isabela Dias Fernandes leads the Tor Project', 'Marcus Holloway leads it'), [], 'names and meaning are not checked');
});
