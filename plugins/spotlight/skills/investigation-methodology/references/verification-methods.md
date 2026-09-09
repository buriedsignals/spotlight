# Verification Methods

How to verify whether an image or video is authentic, original, and represents what it claims. Includes chronolocation — determining when a photo or video was taken using shadows and environmental clues.

---

## The 5Ws Verification Checklist

For any photograph or video, systematically verify:

1. **Originality** — Is this new content, or recycled from an earlier event?
2. **Who** — Who posted it? Are they the original author, or did they reshare it?
3. **Where** — Where was it captured? (See geolocation-methods.md for the full methodology)
4. **When** — When was it captured? (Not just when it was posted — see Chronolocation below)
5. **Why** — Why was it shared? What is the motivation? Any obvious bias or agenda?

### Originality: Detecting Recycled Content

Videos and photographs are frequently recycled for dramatic events — natural disasters, terrorism, conflict. The same footage gets reposted across different contexts with new captions.

**Real-world example:** Night vision footage originally posted in 2009 as "B.o.E Combat Airsoft Night Operations" (a recreational event in Utah) was later recycled as:
- "Karabakh fight night" (Azerbaijan/Armenia conflict, 2016)
- "NIGHT FIGHT KURDS VS ISIS RATS" (Syria/Iraq, 2015, with Kurdish flag added)
- A Russian-titled Syria war video (2016, converted from night vision to black & white)
- Ukrainian news footage from Donetsk Oblast (2016, aired on channel "Ukraina")

Each iteration degraded the quality and added new logos or filters — a common pattern for recycled content.

**Step 1 — Reverse image search the content:**

Use multiple engines — each has different strengths:

| Engine | URL | Best For |
|---|---|---|
| **Yandex Images** | yandex.com/images | Faces, Eastern European content. Often the best first choice. |
| **Google Lens** | images.google.com | General visual similarity, finding copies in different contexts |
| **TinEye** | tineye.com | Exact pixel matching across 32.7B+ images. Finds the earliest known instance. |
| **PimEyes** | pimeyes.com | Facial recognition reverse search (paid) |
| **Bing Images** | images.bing.com | Independent index, sometimes finds what others miss |

**ReVeye** (Chrome extension) runs reverse image searches on 5 engines simultaneously.

**For video:** Capture still frames first, then reverse image search those. Use **InVID WeVerify** plugin (invid-project.eu) to extract keyframes automatically.

**Reverse search tips:**
- Crop or modify the image to hide details that interfere with matching (e.g., crop out a face to search by background)
- **Mirror the image** — sometimes yields different results
- Add keywords to guide the algorithm: image + `"vodka brand name"` narrows results
- If a search returns no results, that does not mean the image is original — it may simply not be indexed

### Who Posted It?

The person who posted content is often NOT the original author. Content propagates through:
- Local WhatsApp groups → larger groups → public social media → news aggregators
- Community groups (shop-and-swap groups, ideological groups focused on specific regions)
- Consider the "why" — do they think anyone is watching?

**Metadata as evidence:** Social media platforms strip metadata on upload. If you can obtain the original file (via email or MMS, not via a platform), metadata may contain GPS coordinates, camera model, timestamps, and editing software history. However, metadata can be fabricated.

### Detecting Photo Manipulation

**Cloning** — The same portion of an image appearing in multiple places. Famous example: 2006 Reuters controversy where smoke columns were clone-stamped in a Beirut airstrike photo to exaggerate the attack.

**Detection tool:** **Forensically** (29a.ch/photo-forensics) — uploads an image and highlights duplicated regions in magenta overlays using Error Level Analysis and clone detection.

**Light sources** — Do shadows and light directions in the image make physical sense? Inconsistent lighting suggests compositing.

**Focus anomalies** — Are objects in both foreground and background in sharp focus? This can indicate two separate photos merged together.

### Think Like a Faker

Fakers deliberately choose source content without clear identifying features. Watch for:
- Zoomed-in crops (hiding context)
- Mirrored images (defeating reverse search)
- Added logos or graphics in corners (obscuring details)
- Dubbed-in or stripped audio
- Filters or quality reduction (making tracing harder)

---

## Determining When Something Was Posted

Social media platforms display time differently. This matters when verifying claims about when something happened.

| Platform | Time Zone Shown | Notes |
|---|---|---|
| **Twitter/X** | Your Twitter settings timezone (Pacific if not logged in) | Check your own settings to interpret correctly |
| **Facebook** | Your device's timezone | Not the poster's local time |
| **YouTube** | Pacific Time (US) | Use InVID or Citizen Evidence Lab for exact UTC time |
| **Instagram** | Relative time ("3d") in Pacific Time | View embed code for exact timestamp |

**YouTube date confusion case study:** Russia's foreign ministry claimed a chemical weapons video was fake because the YouTube upload date appeared to be before the attack. The confusion was caused by YouTube displaying Pacific Time, not local Syrian time.

**To get exact YouTube upload time:** Use **InVID** plugin or **Citizen Evidence Lab YouTube Data Viewer** — returns precise UTC timestamp, view count, channel creation date, and license status.

---

## Chronolocation

Determining the TIME a photo or video was captured by analyzing shadows, weather, and temporary environmental details.

### Visual Clues for Time

Before shadow analysis, look for temporary details that narrow the time window:
- **Gas prices** on station signs (change frequently, narrows to specific weeks)
- **Wet/dry roads** (cross-reference with weather data for the date)
- **Movie posters** on kiosks (theater release windows narrow the period)
- **Seasonal foliage** (bare trees vs. full leaf, specific flower blooms)
- **Construction status** of buildings (compare against known construction timelines)
- **Advertisements** (campaigns have known run dates)

### SunCalc Methodology

**SunCalc** (suncalc.org) calculates sun position, shadow direction, and shadow length for any location, date, and time.

**Step-by-step workflow:**

1. **Establish the location** — You need coordinates (geolocation must be done first)
2. Navigate to **suncalc.org**
3. Set the location by clicking the map or entering coordinates
4. Set the date (known or estimated)
5. Set the object height in the "Object level" field (use approximate height in meters)
6. **Drag the sun icon** on the timeline bar to adjust the time
7. Read the output: **Altitude** (sun angle above horizon), **Azimuth** (compass direction), **Shadow length** and **direction**
8. Compare the shadow direction and length on the SunCalc map overlay to the shadow in your source photo/video
9. For ground-level photos: find a landmark that the shadow overlays, then locate that same landmark on satellite imagery to orient yourself

**Key data SunCalc provides:**
- Dawn, sunrise, solar culmination (peak), sunset, dusk times
- Daylight duration
- Sun altitude and azimuth at any moment
- Shadow length for any object height

**Important insight:** Most satellite images on major mapping services were taken between 11am–1pm local time, to minimize shadow length. This helps calibrate your SunCalc comparison.

### Weather Verification with Wolfram Alpha

Cross-reference visible weather conditions against historical records:

**Query syntax:**
```
weather in [city], [state/country] on [month] [day], [year]
```

Example: `weather in linden, nj on september 19, 2016`

Returns: temperature, conditions (rain/cloudy/clear), humidity, wind speed, precipitation by hour. Compare against what you see in the photo — wet roads should match rain data, clear skies should match sunny conditions.

### Case Study: Linden NJ — Ahmed Khan Rahami Arrest

On 19 September 2016, a tweet showed the arrest of bombing suspect Ahmed Khan Rahami in Linden, NJ. Verification took under 5 minutes:

**Step 1 — Geolocation:**
- Phone number **8211** visible on a sign in the photo
- Google: `"linden, nj 8211"` → Fernando's Auto Sales & Body, 512 E Elizabeth Ave, Linden, NJ 07036, phone 908-486-8211
- Google Street View confirmation: matched yellow bollards, chain-link fence, road sign, white building, manhole cover

**Step 2 — Chronolocation:**
- Wolfram Alpha: `weather in linden, nj on september 19, 2016`
- Result: rain and thunderstorm from ~6am to 12pm, humidity 80-94%, overcast
- The wet road visible in the arrest photo matches the weather data for that morning
- **Conclusion:** The photo is consistent with the claimed date and location

### Case Study: India-China Soldiers Brawl (Pangong Lake, Ladakh)

Alleged time: 15 August 2017, 7:30am. Location: Pangong Lake, Ladakh (elevation 4,248m).

1. Identified shadow direction in the video — shadows appeared perpendicular to the shore
2. Matched features between video frames and satellite imagery from July 2017
3. SunCalc for the coordinates (33.72°N, 78.76°E) on 15 Aug 2017 at 07:31 IST:
   - Altitude: 22.51°, Azimuth: 87.75°
   - Shadow direction consistent with ~7:30am
4. **Conclusion:** Shadows match the claimed time

### Case Study: MH17 Missile Smoke Trail

Russian web users attempted to disprove a photo of the MH17 missile smoke trail by analyzing a shadow on a house roof visible in the high-resolution photo:

1. Identified a house in the smoke trail photo with a barely visible roof shadow
2. Matched the house to Google Maps satellite imagery
3. Set up a tripod at the same location and photographed every few seconds in late afternoon
4. Compared roof shadows from their experiment to the original photo
5. **Their result:** The shadow matched **4:22pm** on July 17, 2014 — just two minutes after MH17 lost contact with air traffic control at 4:20pm
6. They accidentally proved the photo was authentic, consistent with the EXIF timestamp of 4:25pm

This case demonstrates that even extremely small shadows can be used for chronolocation when the location is precisely known.

For tool alternatives (reverse image search, deepfake detection): `/osint` → Video and Image Analysis category.
For country-specific verification tools: OSINT Navigator.
