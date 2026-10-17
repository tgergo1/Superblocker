# Inner-city entry charge for Budapest: plans, world evidence and model calibration

Research notes behind the entry charge in the traffic simulator (`EntryChargeSettings` in `backend/app/models/simulation.py`). Collected 17 October 2026. Prices converted to euros are approximate.

## 1. Where Budapest stands

**No charge is in force, legally authorised or formally scheduled.** Every design figure below comes from studies or plans dated 2008 to 2019.

- **The law blocks it today.** The road traffic act (Kkt.) lets only the government, a minister or a concessionaire charge for road use, not a municipality. In 2024 the Kúria (supreme court) annulled the capital's fee for lorries entering the city on exactly that ground ([Kúria](https://kuria-birosag.hu/hu/kuriai-dontesek/89-torvenyserto-az-onkormanyzati-rendelet-ha-torveny-jeloli-ki-dijszedesre), [adozona](https://adozona.hu/altalanos/Megsemmisitette_a_Kuria_egy_fovarosi_kozgyu_WHXAW2)). A charge on cars needs Parliament to amend the Kkt. first.
- **The city still wants it.** On 10 September 2026, Mayor Karácsony said a charge "would probably solve the financing problems" but is not realistic this term, because it was not in the programme of the Tisza government elected in April 2026 ([VG](https://www.vg.hu/vilaggazdasag-magyar-gazdasag/2026/09/karacsony-gergely-budapest-penzugyek-dugodij)). The transport minister since April 2026, Dávid Vitézy, backed a charge when he ran BKK (2012–2018).
- **It is a standing EU commitment.** The EU funding for metro line 4 (2009) was tied to the city introducing a charge. The commitment was never enforced.
- **It is the only official plan item.** The Budapest Mobility Plan 2030, adopted by the city assembly on 29 May 2019, lists an inner-city entry fee for 2021–2025: 400 HUF a day, paid once a day, residents exempt, 3.3 bn HUF system cost ([VG](https://www.vg.hu/vilaggazdasag-magyar-gazdasag/2019/05/elfogadta-a-kozgyules-a-mobilitasi-csomagot-egyszer-majd-jon-a-dugodij-budapesten), [Telex](https://telex.hu/g7/kozelet/2019/11/13/budapesti-behajtasi-dij-helyett-megcsinalta-a-kormany-a-kihajtasi-dijat)).

### Designs on record

| Source | Zone | Price | Other |
|---|---|---|---|
| Trencon-COWI study for the city, 2013 (the most detailed) | ~40 km² inside the Hungária ring (Pest) and the Buda ring | 400 HUF/day, 07:00–19:00 | ANPR cameras; 18.8 bn HUF/yr revenue with residents exempt; 4.1 bn HUF to build, 2.3 bn HUF/yr to run ([Origo](https://www.origo.hu/itthon/2013/10/szakertoi-tanulmany-a-budapesti-dugdij-bevezeteserol)) |
| BKK, 2012 | Hungária ring and Margit körút | 400–500 HUF/day, ~10,000 HUF a month | Residents probably exempt |
| Mayor Tarlós, 2015–16 | Inside the Kiskörút, plus a Buda strip | ~700 HUF | ~5.5 bn HUF/yr |
| Mobility Plan, 2019 (official) | "Inner city", boundary not drawn | 400 HUF/day | Residents exempt |
| City development agency (BFK) modelling, 2021 | 400 HUF at two crossings | n/a | 55,000 fewer cars a day in the centre ([Index](https://index.hu/belfold/2021/01/21/vitezy_dugodij_galvani_hid_karacsony_gergely_budapest_autosuldozes_kormany/)) |

### Baseline facts

- About 1.2 million people cross the city boundary each weekday: ~755,000 by car and ~447,000 by public transport ([BKK Mobility Plan](https://bkk.hu/downloads/15953/)).
- More than 300,000 cars a day come from the agglomeration.
- The Danube bridges carried about 388,000 vehicles on an average 2024 weekday ([BKK](https://bkk.hu/downloads/32305/)).
- Modal split is roughly 45% public transport and 35% car.
- There were 710,752 cars registered in the city at the end of 2022.

## 2. What charges did elsewhere

### Measured results

| City, start | Scheme | Price (≈ EUR) | Entries | Ring / bypass | Other measured effects | Verdict |
|---|---|---|---|---|---|---|
| London 2003 | Daily area charge | £5 (≈10 today) | −18% (cars ≈ −30%) | Slight rise | Congestion −30% in year 1, back to pre-charge levels by 2006–08 after road space was given to buses and walking; traffic still −21%. Casualties ≈ −40%. NO₂ flat (exempt diesel buses and taxis grew) | Mixed–good |
| London ULEZ 2019–23 | Emissions charge | £12.50 | −5% | n/a | Roadside NO₂ −46% centrally against no ULEZ | Good for air |
| Stockholm 2006/07 | Time-varying cordon, both directions | €1–2 per pass, max €6/day | −20 to −22%, holding at −18 to −20% | Bypass ≈ +5% | Approach-road delays fell sharply; NO₂ −5 to −7.5%; child asthma visits −40%+; support 36% → 53% (referendum) → 70%+ | Good |
| Gothenburg 2013 | Time-varying cordon | €0.8–2.2 per pass | −12% | Norrleden +26%, Landvettervägen +16% | Elasticity eroded from −0.69 to −0.52; 57% voted against it in 2014 | Mixed |
| Singapore ALS 1975 | Area licence, peak | S$3/day | −44% | Inner ring road speeds down to −20% | Congestion shifted to the shoulders; replaced by per-pass ERP (1998) | Good |
| Milan Ecopass 2008 | Pollution charge | €2–10 | −21%, eroding to −16% | n/a | Exempt vehicles rose to 85% of entries | Bad |
| Milan Area C 2012 | Daily charge, 8.2 km² | €5 | −30 to −38%, holding to 2021 | n/a | Casualties −24%; black carbon −30%; 79% yes in the 2011 referendum | Good |
| Bergen 2016 | Peak surcharge on the toll ring | 45 NOK peak | Peak −14%, daily −5% | Shoulder half-hours +11% | Queue time halved | Good |
| Oslo 2017–19 | Environmental and time-varying ring | 54–59 NOK | −3.8% | n/a | Electric car discounts hollowed out the effect | Mixed |
| Trondheim 2005 | Ring removed | 0 | +11% when removed | n/a | Transit and cycling fell back | The effect needs the charge |
| New York 2025 | Daily charge, Manhattan below 61st St | $9 | −11% (≈ 73,000/day) | Outer expressways −5 to −7% | Crossing speeds +23%; subway into the zone +9%; crashes −7%; support 32% → 42%; courts upheld it in 2026 | Good |
| Durham 2002 | One street | £2 | −85% | n/a | Pedestrians +10%. Works like a superblock filter, not a cordon | Good, niche |

Sources: TfL annual reports; [CTS 2012-3](https://www.transportportal.se/SWoPEc/CTS2012-3.pdf), [CTS 2014-25](https://www.transportportal.se/swopec/CTS2014-25.pdf) and [CTS 2017-2](https://www.transportportal.se/swopec/cts2017-2.pdf) (Stockholm and Gothenburg); [ITF on Swedish long-term effects](https://www.itf-oecd.org/sites/default/files/docs/swedish-congestion-charges-long-term-effects.pdf); [KonSULT](https://its.leeds.ac.uk/projects/konsult/test/private/level2/instruments/instrument001/l2_001c.htm); [Danielis on Ecopass](https://sites.units.it/danielis/wp/Ecopass%20-%203%20years%20later,%20finale.pdf); [Milan Area C](https://en.wikipedia.org/wiki/Milan_Area_C); [NY Governor, January 2026](https://www.governor.ny.gov/news/less-traffic-better-transit-its-first-anniversary-governor-hochul-celebrates-transformational); [GLA ULEZ report](https://www.london.gov.uk/programmes-strategies/environment-and-climate-change/environment-and-climate-change-publications/inner-london-ultra-low-emission-zone-expansion-one-year-report).

### Schemes that failed or never started

- **Edinburgh (2005):** rejected in a referendum, 74% against.
- **Greater Manchester (2008):** rejected, about 79% against.
- **Hong Kong (1983–85 trial):** technically positive, but blocked by public opposition.
- **Copenhagen (2012):** dropped over revenue doubts and opposition.
- **San Francisco:** paused after COVID.
- **Jakarta:** ERP delayed for more than ten years.

The common thread: the benefit was not visible before the vote, the revenue use was not trusted, or exemptions grew until the charge stopped working.

### Charges combined with filters

- **Ghent (2017):** circulation sectors with no charge. Peak car traffic −12%, cycling +25%, public transport +28%.
- **London low-traffic neighbourhoods:** traffic inside −48%, boundary roads changed only marginally ([Thomas & Goodman](https://uwe-repository.worktribe.com/output/11457285/changes-in-motor-traffic-in-londons-low-traffic-neighbourhoods-and-boundary-roads)).
- **Barcelona, Sant Antoni:** NO₂ −25%, noise −3.5 dB.
- **Traffic evaporation (Cairns et al., about 100 sites):** traffic on treated roads fell 41% on average, and less than half of it reappeared elsewhere.
- **London's warning:** taking road space away without raising the price brought congestion back while volumes stayed down. Filters and a charge work best together.

## 3. Calibration used in the model

Swedish evaluations measure the elasticity of charged car trips against their total trip cost at introduction:

| Case | Elasticity against total trip cost |
|---|---|
| Stockholm 2006 | −0.70 |
| Stockholm 2009 | −0.85 |
| Stockholm 2014 | −1.24 |
| Gothenburg 2013 | −0.69 |
| Gothenburg 2015 | −0.52 |
| New York (inferred from −11% at a +22–36% cost rise) | −0.4 to −0.6 |

Later price rises give much smaller arc elasticities: −0.09 to −0.26 for London 2005, Stockholm 2016 and Bergen 2016. Introduction is the more elastic event, and Budapest would be an introduction.

**The model uses:**

| Parameter | Value | Basis |
|---|---|---|
| Elasticity on generalised cost, central | −0.7 | Stockholm 2006 and Gothenburg 2013 at introduction |
| Elasticity, low | −0.5 | Gothenburg's eroded value; New York |
| Elasticity, high | −1.2 | Stockholm's long-run value |
| Generalised cost of a trip | Time × 3,000 HUF/h + 60 HUF/km | Roughly 55–70% of the average gross hourly wage; fuel and wear |
| Extra cost for trips from outside the city | +20 min and +15 km | Agglomeration trips continue beyond the modelled area |
| Price, central | 800 HUF per entry | The 2013 study and the 2019 Mobility Plan price of 400 HUF, indexed for inflation since 2013 (about ×1.9) |
| Exempt vehicles | 20% | Disabled badges, taxis, permit deliveries |
| Residents of the zone | Exempt | As in every Budapest design |
| Cordon | Hungária ring in Pest (Róbert Károly, Hungária and Könyves Kálmán körút); Buda ring (Margit körút, Krisztina körút, Alkotás utca, Villányi út, Október 23. utca, Irinyi József utca); the Danube between the bridge heads | The 2008, 2012 and 2013 studies. The model's ring is 32 km², because the Buda side follows the inner körút |

**How the model uses them:**

- **Route choice:** routes see the charge as time, at the value of time, so through traffic can go round the zone.
- **Trip response:** a trip whose route still enters the zone keeps a share `exempt + (1 − exempt) · ((C + charge) / C)^ε` of its demand, where C is the trip's own generalised cost.
- **Interaction with the superblocks:** the time response to the plan (elasticity −0.5) applies on top, so the charge and the superblocks interact through the same equilibrium.

**Boundary roads are free.** Junctions within 60 m of the cordon line count as outside (`boundary_tolerance_m`), so the Hungária ring, the Buda ring and the bridges at the ends of the zone cost nothing to drive along, as London's Inner Ring Road and Stockholm's Essingeleden do.

## 4. What the model found (17 October 2026)

Whole-Budapest run: 205,825 morning-peak car trips, 99 zones, the plan of the same run (2,123 superblocks), 800 HUF per entry, central elasticity −0.7 with −0.5 and −1.2 as the range. The zone has 108 charged entry links and covers 32 km². Changes are against today, once trips respond.

| | Superblocks | Charge | Both |
|---|---|---|---|
| Cars entering the zone (57,600 an hour today) | +11% | −76% (−75 to −79%) | −68% (−66 to −71%) |
| Driving inside the zone | +9% | −44% | −34% |
| Main roads within 400 m outside the zone (the ring) | +49% | +80% | +111% |
| Driving outside the zone | +3% | +8% | +9% |
| Total car-km | +4.1% | +1.0% | +3.3% |
| Average car trip time | +19.4% | +7.9% | +25.8% |
| Main roads more than 10% busier | 773 km | 335 km | 751 km |
| Peak car trips not made | 8.1% | 3.1% | 11.9% |
| NO₂ at the average home (22.27 µg/m³ today) | 22.88 | 22.39 | 22.77 |
| Residents ≥1 µg/m³ more / less NO₂ | 342k / 36k | 61k / 64k | 230k / 63k |
| Residents ≥3 dB louder / quieter | 211k / 308k | 83k / 113k | 222k / 354k |
| Revenue a year (peak hour scaled with a 12% peak share, 250 days) | | 18 bn HUF | 25 bn HUF |

At 400 HUF the charge alone cuts entries by 59% and the ring gains 42%; trip suppression is only 2.5% of trips. Price barely matters to the entry figure, because most of it is rerouting, not trips given up.

**How to read it:**

- **The inner city gets much calmer.** Driving inside the zone falls by a third to a half, and with the superblocks the charge turns the plan's NO₂ result from 342,000 residents worse off into 230,000, and adds about 46,000 residents with quieter homes.
- **The charge does not relieve the main roads the superblocks load outside the zone.** Main roads more than 10% busier: 773 km with the plan, 751 km with both. Trips crossing the city, above all between Buda and Pest, leave the central bridges for the free ring: the Rákóczi bridge goes from about 5,200 to 9,100 vehicles an hour at its busiest section with the charge alone, Könyves Kálmán körút from 4,300 to 8,300. Detours make the average trip longer, so trip time with both (+26%) is worse than with the plan alone (+19%).
- **The entry cut is an upper bound.** Measured cuts at introduction were 12–30% (44% in Singapore). The model's −68 to −79% is far outside that, for two reasons. Budapest's cordon puts the central Danube bridges inside the zone, so much of today's "entering" traffic is cross-river through traffic with a free alternative at the ends of the zone. And a static deterministic assignment moves every driver to the cheapest route, while real drivers differ in their value of time and many would pay; with no traffic counts the model's ring also starts emptier than the real one (Hungária körút peaks at about 3,000 vehicles an hour in the model), leaving more room to divert into. The trip-suppression part (3% of trips at 800 HUF) is in line with the world cases; the diversion part is not.
- **Design implication.** A Hungária-ring cordon with paid bridges and free ring roads mostly moves traffic onto the ring and the outer bridges. Gothenburg is the warning case (+16 to +26% on its bypasses). To relieve the main roads the superblocks load, the charge would need either to keep cross-river traffic charged wherever it crosses (charge the bridges at the ends too, or charge per day for any car driving within the zone or the inner districts, as London and Milan do), or to be paired with capacity limits on the ring, as Stockholm's approach-road metering and London's road-space reallocation did.

Model gaps worth closing before quoting a single number: traffic counts on the ring and the bridges, a multi-class assignment with a spread of values of time, and the per-day (not per-entry) charge of the Budapest designs.
