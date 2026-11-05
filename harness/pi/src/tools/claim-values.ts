// Claim check (Tom, 2026-11-05; pod-base/false-support): judge_finding must not
// turn "supports" into verified for a claim whose number or date its sources
// do not hold. Numbers and dates only, never names or meaning. Formats
// are normalised, so 1.2M is 1,200,000 and 3 March 2024 is 2024-03-03. A value is
// never rounded to match, and word numbers count only on the passage side.
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
const MONTH = '(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\\.?';
const DAY = '(\\d{1,2})(?:st|nd|rd|th)?';
const WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty'];
const SCALE: Record<string, number> = { k: 1e3, thousand: 1e3, m: 1e6, mn: 1e6, mio: 1e6, million: 1e6, b: 1e9, bn: 1e9, billion: 1e9, tn: 1e12, trillion: 1e12 };
const pad = (n: number | string) => String(n).padStart(2, '0');
const month = (name: string) => pad(MONTHS.indexOf(name.slice(0, 3).toLowerCase()) + 1);

type Found = { label: string; keys: string[] };

// Each date as the keys it can match: y-m-d and y-m, or m-d with no year. A
// numeric d/m/y is ambiguous, so it holds both readings.
const DATE_FORMS: Array<[RegExp, (m: RegExpExecArray) => string[]]> = [
	[/\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b/g, (m) => [`${m[1]}-${pad(m[2])}-${pad(m[3])}`]],
	[new RegExp(`\\b${DAY}\\s+(?:of\\s+)?${MONTH},?\\s+(\\d{4})\\b`, 'gi'), (m) => [`${m[3]}-${month(m[2])}-${pad(m[1])}`]],
	[new RegExp(`\\b${MONTH}\\s+${DAY},?\\s+(\\d{4})\\b`, 'gi'), (m) => [`${m[3]}-${month(m[1])}-${pad(m[2])}`]],
	[/\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b/g, (m) => [`${m[3]}-${pad(m[2])}-${pad(m[1])}`, `${m[3]}-${pad(m[1])}-${pad(m[2])}`]],
	[new RegExp(`\\b${MONTH},?\\s+(\\d{4})\\b`, 'gi'), (m) => [`${m[2]}-${month(m[1])}`]],
	[new RegExp(`\\b${DAY}\\s+(?:of\\s+)?${MONTH}\\b`, 'gi'), (m) => [`--${month(m[2])}-${pad(m[1])}`]],
	[new RegExp(`\\b${MONTH}\\s+${DAY}\\b`, 'gi'), (m) => [`--${month(m[1])}-${pad(m[2])}`]],
];

function datesIn(text: string): { dates: Found[]; rest: string } {
	const dates: Found[] = [];
	let rest = text;
	for (const [form, keys] of DATE_FORMS) {
		rest = rest.replace(form, (...match) => {
			dates.push({ label: match[0], keys: keys(match.slice(0, -2) as unknown as RegExpExecArray) });
			return ' ';
		});
	}
	return { dates, rest };
}

const NUMBER = /(?<![\p{L}\p{N}_.,-])(\d{1,3}(?:[,.   ']\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?:\s?(thousand|million|billion|trillion|mio|mn|bn|tn|k|m|b)\b)?(?![\p{L}\p{N}])/giu;

// The values a written number can mean: 1,328 and 1.328 are each a thousands
// form in one locale and a decimal in another, so both readings count.
function readings(written: string): number[] {
	const bare = written.replace(/[   ']/g, '');
	const marks = bare.match(/[.,]/g) ?? [];
	if (!marks.length) return [Number(bare)];
	const last = Math.max(bare.lastIndexOf('.'), bare.lastIndexOf(','));
	const decimal = Number(`${bare.slice(0, last).replace(/[.,]/g, '')}.${bare.slice(last + 1)}`);
	const whole = Number(bare.replace(/[.,]/g, ''));
	if (new Set(marks).size === 2) return [decimal];
	if (marks.length > 1) return [whole];
	return bare.length - last - 1 === 3 ? [whole, decimal] : [decimal];
}

// A form's name is not a value (tor:F2, "Form 990"). Capitalised only: "they
// form 12 groups" keeps its number.
const FORM_NAME = /\bForm\s+\d[\dA-Z-]*/g;

function numbersIn(text: string): Found[] {
	return [...text.replace(FORM_NAME, ' ').matchAll(NUMBER)].map((match) => {
		const scale = match[2] ? SCALE[match[2].toLowerCase()] : 1;
		return { label: match[0].trim(), keys: readings(match[1]).map((value) => String(Number((value * scale).toPrecision(15)))) };
	});
}

const YEAR = /^(?:1[0-9]|20)\d\d$/;

// The claim's values its sources do not hold, as the claim writes them; none
// means nothing is missing. A figure must be in the supporting passages. A date
// or year may be anywhere on the finding's source pages, re-fetched or captured: a passage rarely repeats the
// date of its own release, and on the recorded control a passage-only date check
// left 33% of originals verified, while a page-wide figure check caught nothing,
// since a statistics page holds most figures somewhere (pod-base/false-support).
const MONTH_YEAR = /(?<![\d/.-])(\d{1,2})\/(\d{2})(?![\d/])/g;

export function missingValues(claim: string, passages: string, pages = ''): string[] {
	const own = datesIn(claim);
	const passage = datesIn(passages);
	const held = datesIn(`${passages}\n${pages}`);
	const dateKeys = new Set(held.dates.flatMap(({ keys }) => keys.flatMap((key) => [key, key.slice(0, 7), `--${key.slice(5)}`])));
	const years = new Set([...held.dates.flatMap(({ keys }) => keys.map((key) => key.slice(0, 4))), ...numbersIn(held.rest).map(({ label }) => label)].filter((year) => YEAR.test(year)));
	// u9mac-od3b81 F2: a source's M/YY (Chair as of 9/25) counts only for a claim's bare month and year; it holds no day, number or other date.
	const monthYears = new Set([...`${passages}\n${pages}`.matchAll(MONTH_YEAR)].map((match) => `20${match[2]}-${pad(match[1])}`));
	const numberKeys = new Set(numbersIn(passage.rest).flatMap(({ keys }) => keys));
	WORDS.forEach((word, value) => { if (new RegExp(`\\b${word}\\b`, 'i').test(passage.rest)) numberKeys.add(String(value)); });
	return [
		...own.dates.filter(({ keys }) => !keys.some((key) => dateKeys.has(key) || monthYears.has(key))),
		...numbersIn(own.rest).filter(({ label, keys }) => YEAR.test(label) ? !years.has(label) : !keys.some((key) => numberKeys.has(key))),
	].map(({ label }) => label);
}
