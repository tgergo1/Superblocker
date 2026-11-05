# Data requests to BKK (drafts, updated 2026-11-05 for the EFM user declaration)

Two requests. The first goes through the form on BKK's model page
(https://bkk.hu/rolunk/forgalmi-adatok-diagramok/egyseges-forgalmi-modell/ → "adatigénylő űrlap",
https://forms.office.com/e/a4JAqQ2157). The second is a public-data request (közérdekű adatigénylés)
under Act CXII of 2011 (Infotv.) section 28, sent to BKK's public-data contact listed on
bkk.hu → Átláthatóság → Közérdekű adatok. Fill in the bracketed fields before sending.

---

## 1. EFM 3.0 model data (form)

**Tárgy:** Adatigénylés az Egységes Forgalmi Modellből (EFM 3.0) – kutatási célra

Tisztelt BKK Modellezési Osztály!

Független kutatóként a budapesti szuperblokk-hálózat (forgalomcsillapított lakóterület-csoportok)
hatásait vizsgálom egy nyílt forráskódú eszközzel (https://github.com/tgergo1/Superblocker,
GPL-3.0), és az eredményeket tudományos cikkben publikálom. A modellünk jelenleg nyílt adatokra
épülő gravitációs keresletet használ, amit az EFM adataival szeretnénk kalibrálni és ellenőrizni.

Az alábbi adatokat kérem GIS-ben feldolgozható formában (shapefile/GeoPackage/CSV), a
legfrissebb (EFM 3.0) állapot szerint:

1. A forgalmi körzetek határai és azonosítói (Budapest és agglomeráció).
2. A reggeli csúcsórai (és ha van, napi) **személygépkocsi** körzetközi mátrix (OD mátrix).
3. Ugyanez közösségi közlekedésre, kerékpárra és gyaloglásra, vagy a körzetpáronkénti
   módválasztási arányok.
4. A modellezett közúti hálózat a szakaszonkénti ráterhelt forgalommal (jármű/óra,
   csúcsóra), kapacitással és szabad sebességgel.
5. Röviden: a módválasztási modell paraméterei (rugalmasságok, időérték), ha közölhetők.

**Az adatok felhasználásáról (az EFM felhasználói nyilatkozatához kapcsolódóan):**

- Az adatokat kizárólag a fent leírt feladatra használom: a Superblocker forgalmi modelljének
  kalibrálására és ellenőrzésére, valamint egy tudományos cikk elkészítésére.
- Az átadott EFM adatokat (mátrixokat, hálózati terheléseket) nem teszem közzé, nyilvános
  tárhelyre (pl. a GitHub-tárolóba) nem töltöm fel, és harmadik személynek nem adom tovább.
  Az adatokat saját gépemen tárolom.
- **Engedélyt kérek** arra, hogy az adatokból származtatott, **aggregált eredményeket**
  (pl. az utazási idő és a hálózati kapacitás változása, kalibrációs mutatók, összesített
  táblázatok és ábrák) a cikkben és a projekt dokumentációjában a BKK forrásként való
  feltüntetésével publikáljam. A publikálás előtt szívesen megküldöm a kéziratot
  véleményezésre.
- **Engedélyt kérek** arra is, hogy az adatok feldolgozásához felhőalapú elemzőeszközöket
  (felhőben futó számítási környezetet és mesterséges intelligencián alapuló
  elemzőszolgáltatást) használjak. Ezek az adatokat csak a feldolgozáshoz kapják meg,
  nem teszik közzé.
- A származtatott eredményeket a feladat befejezésekor megküldöm a BKK részére.

Köszönettel:
[Név] · [e-mail] · [telefon]

*English summary:* asks for EFM 3.0 zones, AM-peak car OD matrix, transit/bike/walk matrices or mode
shares, the assigned road network with link volumes, capacity and free speed, and the mode-choice
parameters. In line with BKK's user declaration, it promises to use the data only for this task,
keep it off public hosting and away from third parties, and return derived results; and it asks for
two permissions: to publish aggregated derived results crediting BKK (with the manuscript offered
for review), and to process the data with cloud and AI analysis tools.

---

## 2. Road counter data (public-data request)

**Tárgy:** Közérdekű adatigénylés – Budapest Közút forgalomszámláló adatai

Tisztelt Budapesti Közlekedési Központ Zrt.!

Az információs önrendelkezési jogról és az információszabadságról szóló 2011. évi CXII. törvény
28. §-a alapján az alábbi közérdekű adatok kiadását kérem elektronikus formában (CSV vagy XLSX):

1. A bkk.hu „Közúti mérőeszközök adatai” oldalán megjelenített forgalomszámláló eszközök
   (indukciós hurkok és kamerák) listája: azonosító, helyszín neve, koordináták, mért irány,
   sávok száma.
2. Ezen eszközök mért forgalma **órás (vagy 15 perces) bontásban**, irányonként, 2024. január 1.
   és 2025. december 31. között; ha elérhető, járműkategória szerint.
3. A Duna-hidak keresztmetszeti forgalma ugyanebben a bontásban.

Amennyiben az órás bontás nem áll rendelkezésre, a napi értékeket kérem a fenti eszközökre
és időszakra.

Az adatokat elektronikus úton, a fenti e-mail-címre kérem megküldeni.

Tisztelettel:
[Név] · [lakcím vagy e-mail] · [dátum]

*English summary:* a freedom-of-information request for the list of Budapest Közút counters shown on
bkk.hu (id, name, coordinates, direction, lanes) and their hourly or 15-minute volumes by direction
for 2024–2025, plus Danube bridge counts; daily values as a fallback.
