# Transport Investigation

Methodology for tracking ships and aircraft using open sources. Most public transport is tracked to some degree — all major airliners, most ships, many yachts, some trains.

---

## Maritime Tracking (AIS)

### Core Principle

Ships broadcast their position via **AIS** (Automatic Identification System) transponders. Two primary tracking platforms:

- **MarineTraffic** (marinetraffic.com) — live AIS tracking, vessel details, port calls, vessel database with filtering
- **VesselFinder** (vesselfinder.com) — similar AIS coverage, vessel database filterable by type

### Ship Type Filtering

Both platforms let you filter by vessel type: cargo vessels (general/bulk/container), tankers (oil/chemical/LNG), passenger vessels, high speed craft, tugs, fishing, pleasure craft, navigation aids, military.

### Port Analysis Workflow

To investigate activity at a specific port:

1. Navigate to the port on MarineTraffic
2. Click the port name to see all vessels currently at berth
3. Click "Show All" to open the full **Vessels Database** filtered by current port
4. Database columns: flag, vessel name, destination port, reported ETA, reported destination, IMO number, vessel type, last position time
5. Cross-reference unusual vessels against news reports and satellite imagery

### Tips for Military Vessel Tracking

Not all military vessels broadcast AIS. However:

1. **Track the tugs.** Military vessels are often accompanied by tugs that DO broadcast AIS. Find a confirmed previous port or path of the military vessel, then look for a tug going the same route. Track the tug's movements to infer the military vessel's location.
   - Example: Search for the tug **NIKOLAY CHIKER**, which accompanied the Russian Kuznetsov aircraft carrier.

2. **Satellite corroboration.** Combine AIS tracking data with satellite imagery from **Sentinel Hub** (free). Most ships are visible even in low-resolution (10m) Sentinel imagery. Compare port call timestamps against satellite captures.

### Transponder Deception

WARNING: Vessels engaged in sanctions evasion or sensitive operations deliberately manipulate AIS:

**AIS shutdown:** Transponders turned off mid-voyage. The absence of AIS data at specific locations (e.g., Iranian oil terminals) is itself a significant intelligence indicator. Vessels loading sanctioned oil simply keep their transponders off and load 2 million barrels at a time.

**Destination spoofing:** Vessels declare false destinations in AIS data.

**Real-world example — OTTOMAN TENACITY (29 Dec 2018):**
1. Departed Ceyhan, Turkey (oil terminal)
2. Declared destination: Port Said, Egypt
3. Made a hard turn toward **Ashkelon, Israel** (undeclared)
4. Switched off AIS transponder right before approaching anchorage
5. Load condition: Laden (carrying cargo)

**Detection method:**
- Compare the declared route to the actual track line
- Watch for hard turns away from declared destination
- Note when transponders go dark near specific locations
- Check load condition (Laden = carrying cargo, Ballast = empty)
- Track patterns over time — vessels that repeatedly go dark near the same location

### OSINT Sources for Maritime Intelligence

**Amateur observers at chokepoints:** Enthusiasts at strategic waterways (Bosphorus Strait, Suez Canal, Strait of Malacca) photograph and tweet military and commercial vessels in real-time.

Example: **@YorukIsik** on Twitter monitors Bosphorus transits, providing real-time photographic evidence of Russian military vessel movements.

**TankerTrackers.com** (@TankerTrackers) — tracks oil tanker movements, identifies AIS anomalies, and monitors sanctions evasion. Premium reports available ($19.99/month).

**UNVIM Reports** — The UN Verification and Inspection Mechanism for Yemen publishes weekly situation reports listing vessels at Yemeni ports, clearance requests, inspection status, and port operational status. Useful for corroborating MarineTraffic data against official records.

### Case Study: Blockade of Al Hudaydah (Yemen)

The Yemen port city of Al Hudaydah has been under intermittent blockade by Saudi-led forces.

**Investigation steps:**

1. **MarineTraffic port view** of HUDAIDAH showed vessels at berth: EKRAM, HAPPY VENTURE, VOS THEIA, MERAY GLYFADA, SEA HEART
2. **Vessels Database** filtered by Current Port: HUDAIDAH revealed full details — IMOs, reported ETAs, vessel types, last position times
3. **VOS THEIA** (IMO 9585742) had reported destination "HUDAYDAH W/GUARD O/B" — a World Food Programme vessel attacked off Yemen in June 2018 (Reuters)
4. **HAPPY VENTURE** — UNVIM Report #87 noted this vessel carrying 27,427 metric tonnes of sugar was being held in the Coalition holding area, not permitted by the Coalition's Western Fleet
5. **Satellite corroboration** via Sentinel Hub confirmed vessel positions matched MarineTraffic data

This demonstrates how combining AIS data, UN reports, news coverage, and satellite imagery builds a complete picture of a maritime situation.

---

## Aviation Tracking (ADS-B)

### Core Principle

Most aircraft broadcast their position via **ADS-B** (Automatic Dependent Surveillance–Broadcast). Coverage depends on the density of ground-based receivers.

### Flight Tracking Platforms

| Platform | URL | Key Feature |
|---|---|---|
| **ADS-B Exchange** | adsbexchange.com | **Unfiltered feed** — no military/government blocks. Community-run. Best for military aircraft, law enforcement, and sensitive flights. |
| **FlightAware** | flightaware.com | Commercial flights with detailed logs, strong US coverage |
| **Flightradar24** | flightradar24.com | Live + 365 days history |
| **AirNav RadarBox** | radarbox24.com | General tracking |
| **Planefinder** | planefinder.net | General tracking |

### Why ADS-B Exchange Matters

ADS-B Exchange is the best service for tracking military aircraft. Unlike commercial services, it does NOT filter out:
- Military aircraft
- Law enforcement (police helicopters, DEA planes)
- Government aircraft
- Private flights that have requested filtering on other services

**Filtering options:** Airport, altitude, callsign, country, distance, engine type, ICAO hex, **military**, model code, operator, registration, species, squawk, wake turbulence.

**Data available per aircraft:** Registration, ICAO hex, owner, country, aircraft type, altitude, speed, heading, coordinates, squawk code, engines, transponder type, signal level, time tracked.

### Amateur Photography for Aviation

When ADS-B data is insufficient:
- **JetPhotos** (jetphotos.com) — 4 million+ screened aviation photos, searchable by registration
- **Planespotters.net** — aviation photography and registration data

These sites often have photos of aircraft at specific airports on specific dates, providing visual corroboration of tracking data.

### Private Jet Tracking

Private aircraft tracking depends on whether they have an ADS-B transponder. Some owners request removal from commercial tracking services (e.g., Trump's plane was removed from the FAA tracking list). But ADS-B Exchange, being unfiltered, may still show them.

**Historical data:** Free tier shows limited history; paid subscriptions required for extended historical tracking on most platforms.

For tool comparisons and alternatives: `/osint` → Transport Tracking category.
For country-specific aviation or maritime tools: OSINT Navigator (navigator.indicator.media).
