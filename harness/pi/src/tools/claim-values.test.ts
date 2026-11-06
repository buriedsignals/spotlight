import assert from 'node:assert/strict';
import test from 'node:test';
import { citedFor, missingPeople, missingValues } from './claim-values.ts';

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

// u9mac-od3b81 F2: ProPublica's "Chair (as Of 9/25)" against a claim of "September 2025" downgraded a true finding.
test('a source\'s M/YY holds a claim naming that month and year, and nothing else', () => {
	const row = '|  Christian Kaufmann (Vice-chair (until 9/25) Chair (as Of 9/25))  | $0  |';
	assert.deepEqual(missingValues('Chair as of September 2025.', row), []);
	assert.deepEqual(missingValues('Chair as of Sept 2025.', 'Chair (as Of 09/25)'), []);
	assert.deepEqual(missingValues('Chair as of September 2025.', '', row), [], 'held by the page, as other dates are');
	for (const [claim, source] of [
		['Chair as of August 2025.', row],
		['Chair as of September 2024.', row],
		['Chair as of September 1925.', row],
		['Chair on 3 September 2025.', row],
		['Chair as of September 2025.', 'Chair (as Of 8/25)'],
		['Chair as of September 2025.', 'Page 19/25'],
		['Chair as of September 2025.', 'Filed 1/9/25'],
		['Chair as of September 2025.', 'Filed 9/25/2024'],
	]) assert.equal(missingValues(claim, source).length, 1, `${claim} against ${source}`);
	// The form holds no day.
	assert.deepEqual(missingValues('Chair on 25 September.', row), ['25 September']);
});

// u9mac-verdict2bd8 D1-F5, its real passage: Esra'a Al Shafei's board line never names him.
const D1_F5 = "emmapeel coordinates the localization (translations) of the different Tor software.\n![no-photo](https://tor.eff.org/static/images/people/no_photo.png?h=2690072d)\n##### Esra'a Al Shafei\nEsra'a is on the board of directors for the Tor Project. She is a Bahraini human rights activist.\n![gaba](https://tor.eff.org/static/images/people/gaba.png?h=9fcf056c) [](https://systerserver.town/@gaba)\n##### Gaba\n###### she/her/they";

test('a person the claim names must be in the passage', () => {
	for (const [claim, passage, missing] of [
		['Dennis Jackson is a member of the Tor Project Board of Directors.', D1_F5, ['Dennis Jackson']],
		['Sarah Gran is on the board.', 'Sarah Smith is on the board.', ['Sarah Gran']],
		['Alissa Cooper is on the board.', '![](https://tor.eff.org/static/images/people/alissa.jpeg?h=0a) Leads a project.', ['Alissa Cooper']],
		['isabela is the Executive Director.', '![isabela](https://tor.eff.org/isabela.png)', ['isabela']],
		['Board members include Alissa Cooper and Kendra Albert.', 'Alissa Cooper is on the board.', ['Kendra Albert']],
	] as const) assert.deepEqual(missingPeople(claim, passage), missing, claim);
});

// u9mac-verdict2bd8 D2-F7: its quote names only Alissa Cooper, and no passage names Matt Traudt.
const D2_F7 = "The Tor Project's Board of Directors includes Alissa Cooper, Christian Kaufmann, Dees Chinniah, donuts, Esra'a Al Shafei, Kendra Albert, Matt Traudt, Nathan Freitas, Nick Mathewson, and Sarah Gran.";

test('every person in a list must be in what the verdict cites', () => {
	const quote = '##### Alissa Cooper\nAlissa Cooper is on the board of directors for the Tor Project.';
	assert.deepEqual(missingPeople(D2_F7, quote), ['Christian Kaufmann', 'Dees Chinniah', "Esra'a Al Shafei", 'Kendra Albert', 'Matt Traudt', 'Nathan Freitas', 'Nick Mathewson', 'Sarah Gran', 'donuts']);
	const rows = [quote, '##### Christian Kaufmann', '##### Desigan Chinniah', '##### donuts', "##### Esra'a Al Shafei", '##### Kendra Albert', '##### Nathan Freitas\n##### Nick Mathewson', '##### Sarah Gran'].map((text) => ({ quote: text }));
	assert.deepEqual(missingPeople(D2_F7, citedFor(D2_F7, rows).map((row) => row.quote).join('\n')), ['Matt Traudt'], 'the one nobody names');
	const whole = [...rows, { quote: '##### Matt Traudt' }];
	assert.deepEqual(missingPeople(D2_F7, citedFor(D2_F7, whole).map((row) => row.quote).join('\n')), [], 'a true list, across its passages, stands');
});

test('a claim that names nobody cites its one passage; a named one drops passages about others, and repeats', () => {
	assert.deepEqual(citedFor('Acme paid Doe.', [{ quote: 'a' }, { quote: 'b' }]), [{ quote: 'a' }]);
	assert.deepEqual(citedFor('Sarah Gran is on the board.', [{ quote: '##### Alissa Cooper' }, { quote: '##### Sarah Gran' }, { quote: '##### Sarah Gran' }]), [{ quote: '##### Sarah Gran' }]);
});

test('first names, handles and spellings keep a true claim, and a claim with no person is untouched', () => {
	for (const [claim, passage] of [
		["Esra'a Al Shafei is a member of the Tor Project Board of Directors.", D1_F5],
		['Maria Xynou is a member of the Board.', '##### Maria Xynou\nIRC: agrabeli\nMaria is on the Board of Directors.'],
		['Maria Xynou is a member of the Board.', 'Maria is on the Board of Directors for the Tor Project.'],
		['Isabela Bagueros is the Executive Director of the Tor Project.', '##### isabela\nIRC: isabela\nExecutive Director of the Tor Project.'],
		['isabela is the Executive Director of the Tor Project.', '##### isabela\nIRC: isabela'],
		['mattlav is the Financial Coordinator of the Tor Project.', '##### mattlav\nIRC: mattlav'],
		['Matt Lav is the Financial Coordinator.', '##### mattlav\nIRC: mattlav'],
		['Jacobo Najera is on the team.', '##### Jacobo Nájera'],
		["Esra\u2019a Al Shafei is on the board.", "##### Esra'a Al Shafei"],
		['Former Chair Jane Doe left in 2020.', 'Doe left the board.'],
		['The Tor Project has reported conflict of interest transactions.', 'Transactions were reported.'],
		["Christian Kaufmann is a member of the Tor Project's Board of Directors.", 'Christian is on the board of directors.'],
		['Acme paid Doe.', 'Acme paid Doe.'],
		['The unemployment rate was 6.4%.', 'the rate was 6.4%'],
	] as const) assert.deepEqual(missingPeople(claim, passage), [], claim);
});

