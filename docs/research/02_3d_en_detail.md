# Plan: 3D-weergave en detail bij meerdere doorsneden

Datum: 9 november 2026. Status: plan, gereviewd; de besluiten uit de review staan in §5 en gaan voor. Wens van de gebruiker:

1. Een 3D-weergave in het venster: de doorsneden staan op hun plaats op de balklijn, in kleur, en het beeld
   is te draaien, in te zoomen en van dichtbij te bekijken.
2. Bij meerdere doorsneden er één kiezen om ingezoomd in detail te bekijken.

## 1. 3D-weergave

### Wat de gebruiker ziet

- Een keuze *2D / 3D* bij meerdere doorsneden. In 3D staan dezelfde n doorsneden, met dezelfde nummers,
  kleurschaal, contourlijnen en vinkjes, op hun plaats langs de keten. De keten zelf is een dunne lijn; de
  omtrek van elke doorsnede een donkere lijn. Optioneel een doorzichtige "huid": lijnen tussen de hoekpunten
  van opeenvolgende doorsneden, zodat de vorm van de balk te zien is.
- Draaien met slepen (links), verschuiven met slepen (rechts of middelste knop), zoomen met het wiel naar de
  muis toe. Knop *Passend* zet het beeld terug. Assenkruis X, Y, Z in een hoek.
- Hover geeft nummer, positie en waarde, net als in 2D. Klik op een doorsnede kiest die (zie §2).
- Pijlen voor schuifspanning: in 3D niet in de eerste versie; ze worden onleesbaar in perspectief.
  Contourlijnen wel.

### Hoe het rekent en tekent

Geen nieuwe afhankelijkheden: alles in de werker met numpy, net als nu.

- **Projectie:** orthografisch. Elke doorsnede is vlak, dus de afbeelding van een doorsnede op het scherm is
  een affiene afbeelding. Per doorsnede wordt die afbeelding uit de camera bepaald; elk pixel binnen de
  geprojecteerde omtrek krijgt via de inverse afbeelding een punt (v, w) in de doorsnede, en daarmee een
  driehoek en barycentrische gewichten, precies zoals de huidige pixelkaart. De kleur komt dan uit hetzelfde
  veld als in 2D, dus uit dezelfde rekenkern.
- **Volgorde:** de doorsneden snijden elkaar niet. Achter naar voor tekenen in volgorde van diepte van het
  middelpunt (painter's algorithm) is daarom genoeg, zonder z-buffer. Een doorsnede die bijna op zijn kant
  staat, wordt een lijn; onder een hoek van 2° wordt alleen de omtrek getekend.
- **Kosten:** een nieuwe pixelkaart per draaistap. Bij 6 doorsneden van ongeveer 3000 elementen op
  1200 × 900 verwacht ik 20 tot 60 ms per kaart, te meten. Tijdens het draaien eerst een grof beeld (halve
  resolutie, zonder contourlijnen), bij loslaten het volle beeld. Dat volgt het bestaande patroon met de
  grove tussenweergave.
- **Assen:** de keten levert per element U, V, W en de plaats van het zwaartepunt; de doorsneden staan in
  dezelfde getekende assen als in 2D (gespiegeld bij omgekeerde elementen). Een geknikte keten werkt vanzelf.
- **Schaal:** doorsnede en balklengte verschillen vaak een factor 10 tot 100. Een schuif *Doorsneden
  vergroten* (1 tot 20 keer, standaard automatisch zodat de grootste doorsnede ongeveer 1/(n+1) van de
  balklengte is) houdt het beeld leesbaar; de schaal staat bij het beeld. Zonder vergroting zijn de
  doorsneden van een lange balk onzichtbaar.

### Wat het niet doet

- Geen echte 3D-balk met spanning op de huid tussen de doorsneden: die waarden bestaan alleen op de
  doorsneden zelf.
- Geen perspectief in de eerste versie.

## 2. Detail van één doorsnede

- In 2D-tegels én in 3D: dubbelklik (of klik plus knop *Detail*) op een doorsnede opent die als enkele
  doorsnede op zijn plaats: de schuifbalk springt naar die positie en het venster schakelt naar één
  doorsnede, met alle bestaande functies (zoom, pan, hover, pijlen, markers).
- Een knop *Terug naar n doorsneden* brengt de vorige weergave terug, met dezelfde kleurschaal-instelling,
  het aantal en, in 3D, dezelfde camera.
- De kleurschaal in detail is standaard die van de enkele doorsnede; een vinkje *Schaal van alle doorsneden*
  houdt de gedeelde schaal aan, zodat kleuren te vergelijken blijven.

## 3. Bouw en toetsing

| Stap | Inhoud | Klaar als |
|---|---|---|
| 3D-1 | `beam_render`: affiene pixelkaart per doorsnede, painter's volgorde, camera (draaien, zoomen, verschuiven), grof en fijn beeld | Unit-tests: een vierkante doorsnede onder bekende hoeken valt op de verwachte pixels; volgorde klopt bij twee doorsneden; snelheid gemeten |
| 3D-2 | `beam_worker`: verzoek `frame3d` met camera; hover en klik geven doorsnede en waarde | Protocoltest, ook met oude en nieuwe verzoeken door elkaar |
| 3D-3 | `beam_gui`: keuze 2D/3D, muis, knop Passend, schaal vergroten, NL/EN | Offscreen test met nep-`mw`; beelden in docs |
| D-1 | Detail: dubbelklik en *Detail*, *Terug*, schaalkeuze | Offscreen test; schuifbalk en nummer kloppen |
| T | Proef in eigen verborgen Mecway; documentatie met genummerde beelden; Fable-code-review | Gebruiker beoordeelt het beeld |

Regels als altijd: geen vensters op het scherm van de gebruiker, eigen modellen, niets over zoeken in Mecway.

## 4. Open vragen voor de review

- Is orthografisch met affiene pixelkaarten de eenvoudigste juiste weg, of is een z-buffer over alle
  driehoeken robuuster, bijvoorbeeld bij een keten met een sterke knik?
- Is de vergrotingsschaal voor de doorsneden misleidend, en hoe toon je dat het eerlijkst?
- Muisbediening in een WinForms-besturingselement op een eigen STA-thread in Mecway: valkuilen met
  focus en wiel (het venster mag de focus van de gebruiker niet pakken).

## 5. Besluiten na de review (Fable, 9 november 2026)

Oordeel: bouwbaar na deze wijzigingen. Waar §1 tot §3 afwijken, geldt dit.

| Punt | Besluit |
|---|---|
| Volgorde van tekenen | **Z-buffer per pixel** in plaats van painter's: de diepte is per doorsnede affien in de pixels, dus één vergelijking per pixel. Klopt ook bij een knik, overlappende vergrote doorsneden en een camera langs de balk. Omtrek en markers krijgen dezelfde diepte met een kleine voorsprong. De 2°-regel vervalt voor het tekenen |
| Kosten | **Textuur-warp op de bestaande tegelscenes**: per doorsnede alleen de bedekte pixels via de inverse afbeelding naar de tegel. Contourlijnen, omtrek en kleuren komen mee. Eerste deelstap: meten tegen rasteren van driehoeken, 6 en 12 doorsneden op 1200 × 900. Klaar als de mediaan grof ≤ 30 ms en fijn ≤ 100 ms is |
| Cache | Een apart 3D-slot naast de tegelscenes, zodat draaien de 2D-scenes niet wegspoelt |
| Muis | Wiel werkt pas na een klik in het beeld; het venster activeert nooit zelf. Draaien links, verschuiven rechts of midden. Een dubbelklik begint geen draai. Dubbelklik = *Detail* bij meerdere doorsneden; *Passend* blijft een knop |
| Vergroting | Om het zwaartepunt op de ketenlijn; keten op ware schaal; tekst "doorsneden k× vergroot, niet op schaal"; hover geeft ware waarden; schuif logaritmisch tot 100× |
| Hover op de kant | Bij een slecht geconditioneerde afbeelding geen waarde, wel nummer en positie |
| Detail en terug | Toestand bewaren (aantal, 2D/3D, camera, vergroting, vaste schaal, marker); gedeelde schaal via het schaalveld van het verzoek, niet via de eigen vaste schaal van de gebruiker |
| Huid tussen doorsneden | Vervalt in deze versie |
| Standaardcamera | Zo dat een doorsnede er uitziet als de tegel in 2D; omhoog is Z, of Y als de keten langs Z loopt. Later gewijzigd op verzoek van de gebruiker: als Mecway's standaardbeeld, Y omhoog (Z als de keten langs Y loopt), X naar rechts, van voren. In V2026.2: omhoog volgt Mecway's eigen keuze (*Tools* > *Options* > *General*, opgeslagen als `ViewZTop`, gemeten op Mecway 35), ook bij een balk langs die as; zie de kop van `beam_render3d.py` en `beam_utils.UpAxis` |
| Tests | Uitgebreid met: knik met overlap tegen brute kracht, camera langs de balk, omgekeerd element, op de kant zonder NaN, probe heen en terug, grof gelijk aan fijn, contour niet over grenzen, verborgen omtrek onzichtbaar, protocol met oude en nieuwe verzoeken, toestand na *Detail* en *Terug* |

