# Person Investigation

Methodology for building a profile on an individual by chaining data points together. Every investigation is a pivot chain — you start with one piece of information and use it to discover the next.

**People are lazy.** They reuse 1-2 usernames with slight variations. Even GRU officers used passwords derived from their own name and birth year. Your job is to find these breadcrumbs and chain them together.

---

## Pivot Chain Overview

Example chains — workflows vary with each person:
- Phone number → Telegram → Real name → Email → Breach data → Username → Old accounts
- Car registration → Phone → Skype → Email → Breach database → Password reuse
- Name → Wedding announcement → Maiden name → Social media → Associates
- Username → namechk.com → Abandoned accounts → Date of birth → More accounts

Start with what you have. Pivot to what you can find.

---

## Name Pivots

1. **Google with quotes**: `"First Last"`. Add context if common: employer, city, profession.
2. **Wildcard for middle name**: `"First * Last"` — wedding announcements and court records often include middle names you don't know.
3. **Social media search** — Facebook, LinkedIn, Twitter/X, Instagram, VK (Russian subjects), Odnoklassniki.
4. **Public records** — Court records (PACER in US), voter registrations, property records, business registrations. Highly country-specific.
5. **Non-paywalled newspaper archives** — `site:etypeservices.com "First Last"` and `site:issuu.com "First Last"` for OCR-scanned newspapers.
6. **Life events** — See Life Events section below. Obituaries are the most fruitful source.

## Email Pivots

1. **HaveIBeenPwned.com** — Check breach exposure. Shows which services the email registered for.
2. **Facebook Page Roles** — If you admin any Facebook Page: Settings → Page Roles → type the email. Preview shows account name and photo. **DO NOT click Add** — that would notify them.
3. **Facebook Email Reverse Lookup** (osint.support) — Chrome extension. Returns Facebook user ID from email.
4. **Facebook login error test** — Enter email with fake password. "Password incorrect" = account exists. "Doesn't match any account" = no account. Does not alert the holder.
5. **GHunt** — Reveals Google account data from email: name, reviews, Maps contributions, YouTube channel.
6. **Breach databases** — intelx.io, dehashed.com for associated usernames, IPs, and other emails.
7. **Mail.ru pivot** (Russian subjects) — `username@mail.ru` → `https://my.mail.ru/mail/username/` reveals their Moi Mir profile (birthday, friends, photos).

## Username Pivots

1. **namechk.com** — Checks if a username is in use across dozens of platforms. Refresh page between searches.
2. **Sherlock / Maigret / Blackbird** — Command-line enumeration across 400+ platforms. Maigret has the best coverage.
3. **Check each discovered account** — Each platform reveals different data: date of birth, location, posting history, friend lists.
4. **Try variations** — If you find `john_doe`, also check `johndoe`, `john.doe`, `johndoe1`, `jdoe`.

## Phone Number Pivots

1. **Telegram contact lookup** — Add Contact → type number. If a Telegram account exists, you see their profile. **They are NOT notified** unless you add them or message them.
2. **Skype contact lookup** — Contacts → Add phone number → Save → Search. If they're on Skype, their profile appears with email. Also try typing an email directly.
3. **TrueCaller** — Web-based reverse phone lookup into crowdsourced contact lists. WARNING: Use a dummy email to sign in — signing in with your real account uploads your contact book.
4. **Region-specific apps** — Hundreds exist for nearly every country (Menom3ay for Saudi Arabia, GetContact, Sync.me). Check OSINT Navigator for country-specific options.
5. **Google the number** — Phone numbers appear in business listings, classified ads, court filings, social media posts.

---

## Breach Database Workflow

**Assess your ethical and legal threshold before using breach data.**

**Step 1 — Check exposure:**
- **HaveIBeenPwned.com** — Free. Shows which breaches included the email and which services were affected. Does NOT show actual leaked data.

**Step 2 — Access breach data (if within threshold):**
- **Intelligence X** (intelx.io) — Most comprehensive. Breach data, darknet content, WHOIS records.
- **DeHashed** (dehashed.com) — Breach database search (paid).

**Step 3 — Pivot from breach data:**
- **Password reuse** — Distinctive passwords searched across databases reveal unknown accounts.
- **Username discovery** — Breach records contain usernames for the breached service, which may differ from the email.
- **IP addresses** — Some datasets include registration/login IPs.
- **Associated emails** — Secondary email addresses linked to the same account.

---

## Facebook Default URL Trick

Facebook assigns default URLs based on the registration name: `facebook.com/first.last1`. Higher numbers = later registrations with that name.

**Investigation technique:**
- Higher-numbered URLs often belong to people who **changed their display name** after registration — the URL preserves the original name.
- Search `facebook.com/first.last1`, `first.last2`, etc. for your target.
- Useful for finding profiles under fake display names when you know the real name.

**Bilal Hadfi case:** Normal name searches for the November 2015 Paris suicide bomber returned nothing. Navigating directly to `facebook.com/bilal.hadfi1` found a profile under the display name "Bilel Berkani (LeViiruus)" — his actual account containing extremist content.

## Archived & Deleted Profiles

Platforms remove profiles of suspects after incidents. Internet sleuths often archive them first.

1. Guess the URL using default patterns (`facebook.com/first.last`, `twitter.com/username`)
2. Search **Archive.today** (archive.today) for cached snapshots
3. Search **Wayback Machine** (web.archive.org) for historical captures

**Sayfullo Saipov case:** After the 2017 NY truck attack, `facebook.com/sayfulloh.saipov.1` was removed. Archive.today had two captures showing the complete profile: photos, school, location, 17 friends with names visible.

---

## Life Events Research

### Obituaries — Most Valuable Source

Out of weddings, births, and deaths, **obituaries are the most fruitful**:
- Extended family listed (often without the subject's knowledge or consent)
- Published more universally than wedding or birth announcements
- The word "obituary" has no confusion with other topics, making searches precise
- Archives go back decades, often digitized

**Search:** `"First Last" obituary`, legacy.com, newspapers.com, findagrave.com

**Key insight:** Search for obituaries of the subject's **relatives** — parents, siblings, grandparents. The obituary will name your subject as a surviving family member.

### Weddings

Wedding announcements reveal: full names (including maiden), parents' names, wedding party members, occupations, employers, locations.

**Search:** `"First Last" wedding announcement`, theknot.com, withjoy.com, gift registries (Amazon, Crate & Barrel, Target)

**Key insight:** Search relatives' weddings — a sibling's announcement may name parents, other siblings, and extended family.

### Births

Birth announcements reveal: parents' full names, date, hospital/city, siblings, sometimes grandparents.

**Search:** `"First Last" "birth announcement"`, baby registry sites

---

## Case Study: Badin Pivot Chain (GRU Officer)

The Bellingcat investigation of GRU officer Dmitriy Sergeyevich Badin demonstrates the complete methodology:

1. **Car registration** (Russian vehicle registry) → revealed Badin's **phone number**
2. **Phone number** → searched on Skype → revealed his **Skype account**
3. **Skype account** → revealed his **email**: `badin1990@mail.ru`
4. **Email** → searched in breach databases (Intelligence X) → found in Collection #1 breach
5. **Breach data** → revealed passwords: first `Badin1990`, later `Badin990`, then `397077014dimon`

Even GRU hackers — professionals whose job is cyber security — used passwords derived from their own name and birth year. This pivot chain (car registration → phone → Skype → email → breach data) shows how a single starting point cascades through platforms to reveal compromising information.

## Case Study: Serebryakov (Mail.ru Pivot)

After Dutch security services published GRU agent Yevgeny Serebryakov's email (`ryback_casey@mail.ru`):

1. Applied the Mail.ru → Moi Mir pivot: `https://my.mail.ru/mail/ryback_casey/`
2. Found profile showing birthday: **26 July 1981**
3. Birthday **exactly matched** the date on his Russian passport (26.07.1981) — confirming the account belonged to the GRU agent

## Case Study: Khashoggi Suspects (Contact Book App)

A suspect in the Khashoggi case traveled with another person's passport. Searching the phone number in **Menom3ay** (Saudi contact book app) identified him as a royal guard member. A guard wearing that name appeared in a 2017 video standing next to Prince Mohammed.

For tool comparisons and alternatives: `/osint` → People Search category.
For country-specific lookup tools: OSINT Navigator (navigator.indicator.media).
