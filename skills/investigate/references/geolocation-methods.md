# Geolocation Methods

How to determine where a photo or video was taken by comparing visual details against reference imagery. Geolocation is the single most useful method for verifying digital content.

---

## The 4-Step Methodology

### Step 1: Reverse Image Search

Before manual analysis, check if someone has already geolocated this content.

Use **Yandex Images** (yandex.com/images) first — it outperforms Google for faces and Eastern European content. Then try **Google Lens** (images.google.com), **TinEye** (tineye.com), and **Bing Images** (images.bing.com).

For video: extract still frames using **InVID WeVerify** plugin (invid-project.eu) or take manual screenshots of distinctive moments.

### Step 2: Identify Visual Features

Systematically catalog everything visible that could narrow the location:

**Text and signage:**
- Language and script (Georgian, Arabic, Cyrillic, etc.)
- Country code TLDs on signs (.ge, .ru, .br)
- Phone number formats
- Street names, business names, transit stop names

**Infrastructure:**
- Road markings and driving side (left/right)
- License plate format and color
- Power line and utility pole design
- Road surface type and condition
- Traffic signal style
- Bridge design

**Architecture:**
- Building style (Haussmann, Soviet bloc, colonial, etc.)
- Roof types, window patterns, balcony styles
- Construction materials (stone, brick, concrete, wood)
- Religious buildings (mosque minarets, church spires, temple roofs)

**Landscape and vegetation:**
- Tree species and vegetation density
- Terrain type (flat, mountainous, coastal, desert)
- Soil color
- Agricultural patterns

**Unique identifiers:**
- Landmarks (towers, monuments, distinctive buildings)
- Mountain silhouettes (matchable with **PeakVisor**)
- Metro/subway station design
- Commercial chains with limited geographic presence
- Military equipment markings

**BE CREATIVE.** Anything visible that can be seen from above or matched against reference imagery is useful.

### Step 3: Compare with Reference Imagery

Match your identified features against:

**Satellite imagery** (see Satellite section below):
- Google Earth Pro, Bing Maps, Yandex Maps — compare features visible from above
- Look for road patterns, building footprints, distinctive structures

**Street-level imagery:**

| Provider | URL | Coverage |
|---|---|---|
| **Google Street View** | maps.google.com | Near-worldwide, historical imagery available |
| **Yandex Panorama** | maps.yandex.com | Russia, Ukraine, Belarus, Turkey, Central Asian cities |
| **Bing Streetside** | maps.bing.com | US + some European cities |
| **Apple Look Around** | Apple Maps | Limited but growing |
| **Mapillary** | mapillary.com/app | Crowdsourced dashcam/bike imagery — covers places others miss |

**User-uploaded geotagged content:**
- Google Maps photos, Yandex Maps photos, Flickr, Instagram location tags, VK, Foursquare, TripAdvisor
- WARNING: User-supplied geotags must be verified — users can misplace them

**Wikimapia** (wikimapia.org) combines satellite imagery with user-named locations. Extremely useful in countries where Google has not labeled locations.

### Step 4: Document and Verify

- Be explicit about every step and assumption
- Find **several different matching elements** before determining a location — a single match is not enough
- Document with screenshots and annotations
- Archive evidence before it may disappear

---

## Satellite Imagery

### Resolution Comparison

| Satellite | Resolution | Access |
|---|---|---|
| Aqua (MODIS) | 250m per pixel | Free (NASA) |
| Landsat-8 | 30m per pixel | Free (USGS) |
| **Sentinel-2** | 10m per pixel | Free (ESA Copernicus) |
| PlanetScope (Dove) | 3m per pixel | Free tier available (Planet Labs) |
| Pleiades | 0.5m per pixel | Commercial (~$1,000-2,000/image) |
| Worldview-4 | 0.3m per pixel | Commercial (Maxar) |

Most imagery on Google Maps comes from Maxar (formerly DigitalGlobe) at 50cm or better for urban areas.

### Google Earth Pro vs Sentinel Hub

**Google Earth Pro** (google.com/earth — free download):
- Highest resolution free imagery (sourced from Maxar)
- **Historical imagery slider**: View → Historical Imagery shows changes over time
- Shows imagery date
- Terrain view for matching perspectives from ground-level photos
- 3D buildings in major cities
- Best for detailed location matching

**Sentinel Hub EO Browser** (apps.sentinel-hub.com/eo-browser):
- Lower resolution (10m) but **much more frequent** captures
- Multiple images per week for most locations
- Better when you need imagery from a specific date
- NOT censored — Google blurs sensitive military/government sites, Sentinel does not
- Multiple rendering bands reveal different information:

| Rendering | Bands | Reveals |
|---|---|---|
| Natural color | 4, 3, 2 | Standard visible light |
| Color infrared | 8, 4, 3 | Vegetation health |
| False color urban | 12, 11, 4 | Built structures |
| SWIR | 12, 8A, 4 | Short-wave infrared |
| Atmospheric penetration | — | Sees through haze |

### Satellite Imagery Date

Each provider shows imagery dates differently. Always check:
- **Google Earth Pro:** Date displayed directly in the imagery bar
- **Google Maps:** Copyright line at bottom (e.g., "Imagery 2020 CNES / Airbus, Maxar Technologies")
- **Bing Maps:** Copyright line (e.g., "2020 DigitalGlobe")
- **Yandex Maps:** Attribution line (e.g., "Airbus DS 2017")

**Compare dates across providers.** Google, Bing, and Yandex often have imagery from different years for the same location. Use all three to find the most relevant date for your investigation.

### Case Study: Saudi Oil Refinery Attack (14 September 2019)

Coordinates: 25.917341, 49.704740

- Google Maps/Earth **blurs this location** (sensitive Saudi infrastructure)
- Sentinel Hub does NOT censor — comparing imagery from 12 September (pre-attack) vs 15 September (post-attack) clearly shows fire damage
- This demonstrates why relying on a single satellite source is insufficient

### Historical Imagery

Google Earth Pro's historical imagery slider is essential for tracking changes over time: construction progress, building destruction, military deployments, environmental changes.

**No satellite available?** Search YouTube for drone footage: `[location name] DJI` or `[location name] drone`. Filter by 4K resolution. Even tiny towns (under 6,000 people) often have drone videos uploaded by enthusiasts.

### Heat and Fire Detection

**Global Forest Watch Fires** (fires.globalforestwatch.org/map):
- VIIRS and MODIS active fire data
- Current and historical
- Turn "Fires" ON, "Air Quality" OFF, switch base map to Imagery
- Search by coordinates, select date range

---

## Case Study: Finding ISIS Supporters in Paris

A photo posted on Twitter showed a hand holding pro-ISIS text, viewed from a window looking down onto a street.

**Step 1 — Visual clues:**
- European-style Haussmann architecture visible
- A **Suzuki motorcycle dealership** logo visible in the background street

**Step 2 — Feature matching:**
- Google Street View identified the dealership as "SUZUKI MOTO CHAMPION" on a specific Paris street
- Building facades, rooflines, and window patterns matched between the photo and Street View

**Step 3 — Precise location:**
- The photo was taken from a building directly across the street from and above the Suzuki Moto Champion dealership
- Specific apartment building identified

A single commercial sign in the background was enough to geolocate the exact building.

## Case Study: Finding Werfalli (ICC Investigation)

The ICC issued an arrest warrant for a Libyan commander based entirely on social media evidence. Bellingcat crowdsourced the geolocation of an execution video that the ICC could not locate:

- Identified buildings, a wall, and a fork in the road visible in the video
- Matched these features against Google Earth satellite imagery
- Confirmed the location as a military compound between Benghazi and its airport, controlled by the commander
- This geolocation was submitted as evidence to the International Criminal Court

This case demonstrates that geolocation from social media can directly support international criminal prosecution.

---

## Additional Resources

**Liveuamap** (liveuamap.com) — real-time news mapped geographically for conflict zones (Yemen, Ukraine, Syria, Libya, Venezuela).

**Industry About** (industryabout.com) — maps of industrial facilities worldwide with coordinates. Useful for identifying factories, mines, and processing plants in satellite imagery.

**@quiztime on Twitter** — daily geolocation practice challenges from the verification community.

For tool comparisons and alternatives: `/osint` → Geolocation or Satellite Imagery categories.
For country-specific mapping tools: OSINT Navigator (navigator.indicator.media).
