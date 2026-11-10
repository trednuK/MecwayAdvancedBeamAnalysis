# Vooronderzoek: spanningen op elk punt van een balkdoorsnede in Mecway

Datum: 7 november 2026. Status: voorstel, nog niets gebouwd. Alle metingen op Mecway 35.0.9802 en
Code-Aster 17.4, met eigen voorbeeldmodellen. Mecway 34 is hier niet getoetst.

## 1. Conclusie vooraf

- **De tool rekent zelf, op elke doorsnede, en niet de bridge.** Spanning op een balkdoorsnede is
  nabewerking: uit zes snedekrachten en de vorm van de doorsnede volgt het hele veld. Mecway's eigen
  solver, CalculiX via Mecway en de bridge leveren die krachten.
- **Er is geen open-source tool om over te nemen, wel de methode.** Die van Pilkey, die
  `sectionproperties` gebruikt en `section_fem.py` in MecwaySectionProps al uitvoert. Op open-aec en in
  de FreeCAD-gemeenschap vond ik geen veld over een willekeurige doorsnede dat werkend gekoppeld is.
- **De rekenkern is bewezen haalbaar.** In een proefje klopten de resultanten op 1e-10 en de puntwaarden
  op 2e-5 tot 2e-3, afhankelijk van de vorm. De doorsnede rekent in 0,1 s.
- **Het venster kan vloeiend.** Een echt beeld kost in de mediaan 2 tot 28 ms, buiten Mecway gemeten. Het
  eerste beeld duurt ongeveer 2 s, omdat Python dan start.
- **Het dummy-element werkt, met één beperking.** Het is alleen zichtbaar in het modelbeeld, niet zolang
  Mecway de oplossing toont. Dat is gemeten en met een opname bevestigd (§7).
- **De wringing vraagt aandacht.** De solvers rapporteren T om de balk-as, terwijl de schuifvelden door
  het schuifcentrum lopen. Zonder omrekening is de wringspanning op U- en L-profielen fout. De formule is
  nu gemeten en staat in §5.2.

## 2. Wat de tool moet doen

Samengevat uit de beschrijving: een eigen venster; balkelementen kiezen uit een selectie in Mecway; de
doorsnede tonen met spanningen op elk punt in kleur, en stromen als vectoren; een schuifbalk voor de
plaats langs de balk, of meerdere doorsneden gelijk verdeeld; spanningen aan- en uitvinken met direct
bijwerken; en als extra de positie in Mecway tonen met een dummy-element. Rechte balk als uitgangspunt.
Alles open source en commercieel bruikbaar binnen het bedrijf.

## 3. Huidige stand van de zusterprojecten

### MecwayAsterBridge (`develop`, V2026.8)

| Onderdeel | Stand |
|---|---|
| Snedekrachten | `beam_forces` staat voor statisch standaard aan: `tensileforce`, `sheary`, `shearz`, `torsion`, `bendingmomv`, `bendingmomw`, per knoop en per element-knoop (`EFGE_ELNO`) |
| Balkspanning | `beamsixxmin/max` alleen voor rechthoek en cirkel. `SIPM_ELNO` is op GENERALE leeg (alarm en nullen), `SIPO_ELNO` geeft per component één uiterste waarde en geen veld (gemeten) |
| `longitudinalstress` | S37, `bridge_section_view.py`: σ = N/A + Mv·w/Iyy − Mw·v/Izz op de geveegde schillen. Geen schuif, wringing, welving of von Mises. Op een Code-Aster-oplossing NOT verified |
| Vorm van de doorsnede | `** SECTIE-VORM`-regels (van MecwaySectionProps), `** SECTIE-RICHTING`, `#ASTER-SECTIE` |
| Tijdelijke elementen | `bridge_section_model.py`: toevoegen en verwijderen, ook na crash. Geen effect op de oplossing, gemeten op Static 3D |
| Welving | S19 `POU_D_TG`: 7e vrijheidsgraad, voor knik. `BX` (bimoment) wordt niet geëxporteerd |
| Schuifcentrum | `shear_centre_warning` waarschuwt alleen als `JG` aanwezig is en de afstand > 0,1·√A |

**Stappenplan.** S41 (open, prioriteit): gedetailleerde spanning op willekeurige doorsneden, tonen in
ParaView, en uitzoeken of Mecway het ook kan tonen. S39 en S40 (open) gaan over schermen en raken dit
niet. S25 is vervallen ten gunste van MecwaySectionProps. Dit voorstel vult S41 in; zie §9.

### MecwaySectionProps (V2026.2)

Deze repo heeft geen stappenplan: alleen `CHANGELOG.md` en de beperkingen in de documentatie (§7 daar).
Relevant voor ons:

- `section_mesh.py`: eigen tri6-mesher op scipy, zonder de bibliotheek Triangle. Juridisch een voordeel (§4).
- `section_fem.py`: formulering van Pilkey; lost ω, ψ en φ op. Alleen ω komt naar buiten (`_omega`).
- Beperkingen die wij erven: geen J, Iw en schuif bij losse delen; vlakke doorsneden.
- Contract: `SECTIE-VORM` staat in hoofdassen om het zwaartepunt, V = Mecway V, W = U × V. De punten zijn
  afgerond, dus een hermeshte vorm heeft een kleine Ixy.
- Een doorsnede met alleen eigenschappen, zonder vorm, is niet tekenbaar. Die krijgt een melding.

### Een aparte nabewerkingstool

Een aparte tool raakt het onderwerp direct, dus ik heb die ook bekeken.

- Een filter toont spanning op I, T, L, C, rechthoek en cirkel in ParaView, met **dunwandige
  benaderingen** voor schuif en wringing. Generieke doorsneden worden overgeslagen.
- Uitbreiden van de filter en terugkoppelen naar Mecway staan daar nog open. De filter leest de uitvoer
  van de bridge nog niet volledig.
- Nieuw uit de meting in §5.2: de filter gebruikt MT direct, terwijl de schuifstroom door het
  schuifcentrum loopt. Bij een excentrisch schuifcentrum mist de term `EY·VZ − EZ·VY`.

## 4. Beschikbare tools

Licenties gecontroleerd in de licentiebestanden zelf, op 7 november 2026.

| Tool | Licentie | Wat het doet | Oordeel |
|---|---|---|---|
| [sectionproperties](https://pypi.org/project/sectionproperties/) 3.10.2 | MIT | Willekeurige doorsnede, ω/ψ/φ, spanning per snedekracht, composiet | Beste referentie. **Niet als afhankelijkheid**: `cytriangle` is verplicht, dus Triangle komt mee |
| [cytriangle](https://pypi.org/project/cytriangle/) | LGPL-3.0 voor de wrapper | Mesher onder sectionproperties | De meegeleverde Triangle-code houdt zijn eigen voorwaarden |
| [Triangle](https://github.com/libigl/triangle) | Eigen licentie, niet OSI | Mesher | Gratis voor privé, onderzoek en instituten. Distributie in een commercieel systeem alleen na afspraak. Intern gebruik door een bedrijf wordt niet genoemd. **Vermijden** |
| [xara](https://github.com/peer-open-source/xara) | BSD-2-Clause | OpenSees-omgeving | Bruikbaar, maar groot en niet nodig |
| [xsection](https://pypi.org/project/xsection/) | **Geen licentie gevonden** | Doorsnedeanalyse | Niet gebruiken |
| [ANBA4](https://github.com/ANBA4/anba4) | GPL-3.0 | Anisotrope doorsnede | Ander doel: composieten |
| [BECAS](https://becas.dtu.dk/) | Betaald, € 3.000 tot 9.600 per jaar | Anisotrope doorsnede | Valt af: niet open source |
| [CUFSM](https://www.ce.jhu.edu/cufsm/repository/) | MIT | Eindige stroken, knik | Ander doel |
| [VABS, SwiftComp](https://analyswift.com/) | Gesloten | 3D-spanning uit balkresultaten | Alleen concept |
| Code-Aster `MACR_CARA_POUTRE` | GPL | Doorsnede-eigenschappen, geen spanningsveld | Controle-instrument, zit al in de tests van MecwaySectionProps |

**open-aec.** De [Open AEC Foundation](https://open-aec.com/en/) heeft geen losse balkdoorsnede-tool.
Gevonden, na lezen van de code:

| Onderdeel | Licentie | Inhoud |
|---|---|---|
| [open-fem-studio-mechanics](https://github.com/OpenAEC-Foundation/open-fem-studio-mechanics), `vendor/dedaliano-engine/src/section/` | AGPL-3.0 | Bouwstenen voor willekeurige doorsneden (Prandtl, schuifveld, Cw). Zover ik zag alleen in tests gebruikt, niet gekoppeld aan de spanningsuitvoer |
| idem, `postprocess/section_stress*.rs` | AGPL-3.0 | Per vorm analytisch, 31 punten langs elke as. Een willekeurige vorm wordt als rechthoek behandeld. Geen welving |
| idem, crate `section-properties` | LGPL-3.0-or-later | Eigenschappen, geen spanningsveld |
| [Open-CalcSheet-Studio](https://github.com/OpenAEC-Foundation/Open-CalcSheet-Studio), `doorsnede*` | LGPL-3.0 | σ, Jourawski, von Mises, kleurveld in stroken, diagrammen langs de assen. Standaardvormen en samengestelde rechthoeken; geen wringing of welving |

Bruikbaar als **concept**, niet als code: AGPL past niet in een MPL-repo, LGPL-code overnemen in
MPL-bestanden is juridisch lastig, en de talen passen niet bij Mecway. Overige repo's alleen op padnaam
bekeken; 70+ repo's van de organisatie zijn niet volledig doorzocht.

**FreeCAD.** In de installatie 1.1 (`Mod/Fem`, `Mod/BIM`) staat niets over spanning op een doorsnede. De
FEM-werkbank kent alleen rechthoek, cirkel en buis, en CalculiX expandeert balken naar volume-elementen.
Snedekrachten op lijnmeshes zijn een [open wens](https://github.com/FreeCAD/FreeCAD/issues/5718). De
addon-catalogus bevat [StructureTools](https://github.com/FreeCAD/FreeCAD-addons) (LGPL-3.0: A, Iy, Iz,
N/V/M-lijnen) en StandardBeams (tabellen); geen welving, schuifcentrum of spanningsveld. FrameForge,
SteelColumn en OSAFE zijn niet inhoudelijk gelezen. Forum en wiki alleen via zoekopdrachten.

**Gangbare werkwijze in andere pakketten.**

- Commerciële pakketten rekenen de doorsnede zelf uit de snedekrachten, dus de solver maakt niet uit.
- Eén of meerdere elementen en gelijkverdeelde doorsneden zijn een bekend patroon.
- Als concept bruikbaar: keuze van de spanningsmaat (von Mises, maximale schuif, hoofdspanningen, axiaal,
  schuif in twee richtingen, gecombineerde schuif als vectorbeeld), vaste schaal, instelbare lengte van
  vectoren en fijnheid van het rekenmesh.

## 5. Theorie en metingen

### 5.1 Basis

Saint-Venant (prismatische balk, lineair elastisch) met de formulering van Pilkey. Per doorsnede worden
drie Poisson-problemen opgelost op een tri6-mesh: ω voor wringing, ψ en φ voor dwarskracht, met ν in de
vergelijking. Daarna is de spanning lineair in de snedekrachten. Met D = Ivv·Iww − Ivw²:

```
σ(v,w) = N/A + (Mv·Iww + Mw·Ivw)·w/D − (Mw·Ivv + Mv·Ivw)·v/D     (tekens: gemeten, §7)
τ(v,w) = Vv·τ̂_Vv(v,w) + Vw·τ̂_Vw(v,w) + T_S/J·(∇ω − (w, −v))
```

Ivv is hier het traagheidsmoment om de V-as, zoals Mecway's `iyy`. In hoofdassen is Ivw = 0, maar de
afgeronde `SECTIE-VORM` geeft een kleine Ivw, dus de tool rekent altijd met de algemene vorm. τ̂_Vv en
τ̂_Vw zijn de eenheidsvelden uit ψ en φ.

Die lineariteit maakt "direct dynamisch" mogelijk: eenmaal per doorsnede de eenheidsvelden, daarna alleen
optellen. Von Mises is √(σ² + 3τ²), want de overige spanningen zijn in deze theorie nul.

Proefje (`section_mesh` en `section_fem` ongewijzigd, Python van Code-Aster):

| Controle | Uitkomst |
|---|---|
| Rechthoek, dwarskracht, ν = 0: τmax / (1,5 V/A) | 1,00002 |
| Rechthoek, som τ dA = V, moment om zwaartepunt = 0 | 1e-13 |
| Rechthoek, wringing: moment van τ = T | 2e-15 |
| Cirkel, wringing: τmax / (T·r/J) | 0,998 |
| I-profiel, lijf op neutrale lijn: τ / (VQ/It) | 1,0000 op vier mesh-fijnheden |
| U-profiel: moment van de schuifspanning om zwaartepunt / (V · afstand schuifcentrum) | 1 op 1e-12 |
| Rekentijd rechthoek, I-profiel (±3000 elementen) | mesh 0,05 s, FEM 0,08 s |
| Rekentijd I-profiel, 50 000 elementen | FEM 1,6 s |

### 5.2 Schuifcentrum

De eenheidsvelden voor dwarskracht werken door het schuifcentrum S. De solvers rapporteren T om de
balk-as, en die as is het zwaartepunt G. Gemeten in Code-Aster op `POU_D_E`, `POU_D_T` en `POU_D_TG`, met
120 cases en een fout van 4,5e-8:

```
T_S = MT + EY·VZ − EZ·VY         EY, EZ = vector van S naar G, lokale assen y = V, z = W
```

- MT in `EFGE_ELNO` is de **totale** torsie om de meshlijn, ook op `POU_D_TG`, ook aan de inklemming.
- Code-Aster gebruikt EY/EZ bij alle drie de elementen: de hypothese "genegeerd" is weerlegd.
- Mecway's eigen balk kent geen EY/EZ, maar T is daar ook om de as. Voor de gegeven snedekrachten geldt
  dus dezelfde formule, met EY/EZ uit de doorsnede. Tekens van `torsion`, `sheary`, `shearz`: zelfde
  afspraak (§7). **Beperking:** die balk modelleert de koppeling tussen buiging en wringing niet, dus in een
  statisch onbepaalde constructie zijn T en V zelf al anders dan in werkelijkheid. Dat kan de tool niet
  herstellen, en het venster meldt het bij een excentrisch schuifcentrum.
- Voorstel: omrekenen is de standaard, met een schakelaar, en de term staat apart zichtbaar.
- S volgt uit dezelfde berekening als de velden, dus de tool kent EY en EZ altijd. Staat er een afwijkend
  `#ASTER-SECTIE` in het model, dan telt voor een Code-Aster-oplossing die waarde, met een melding.

### 5.3 Scherpe inwendige hoeken

Op een I-profiel zonder afronding groeit de hoogste spanning mee met het mesh, want de hoek is singulier.
De piek is daar geen uitkomst maar een artefact.

| Elementen | Dwarskracht, hele doorsnede | Wringing, hele doorsnede | Wringing, buiten r = 1 t |
|---|---|---|---|
| 2 047 | 1,24 | 1,76 | 1,035 |
| 3 832 | 1,44 | 2,09 | 1,044 |
| 13 413 | 1,78 | 2,52 | 1,050 |
| 50 402 | 2,27 | 3,26 | 1,054 |

Waarden als veelvoud van VQ/It en T·t/J. Een kleinere straal (0,5 t) is te krap: het maximum zit dan nog
in de hoek. De dwarskracht zegt niets over de zone, want dat maximum zit midden in het lijf.

Voorstel, als vuistregel: rond elke inspringende hoek een zone met straal 1 × de plaatselijke dikte. Die
dikte is de afstand van de hoek tot de dichtstbijzijnde rand die niet aan de hoek grenst. Het maximum
buiten de zones is het hoofdgetal, met ongeveer 2 % mesh-afhankelijkheid; de tool vergelijkt het op twee
mesh-fijnheden en meldt een groter verschil. De piek staat erbij, met een markering in het beeld. Op
andere vormen dan dit I-profiel is deze regel nog niet getoetst. Wie de echte piek wil weten, moet de
afronding in de doorsnede opnemen.

### 5.4 Verloop binnen een element bij verdeelde last

Gemeten op Mecway's eigen balk (line2), cantilever 1 m. Lijnlast langs V en W, schuine balk en
zwaartekracht geven hetzelfde patroon:

| Grootheid | Eén element | Vier elementen | Exact aan de inklemming |
|---|---|---|---|
| Mw, q_v = −1000 N/m | −416,67 | −494,79 | −500 |
| Mv, q_w = −1000 N/m | 416,67 | 494,79 | 500 |
| Vv, eerste element, q_v = −1000 N/m | −500 | −875 | −1000 |
| Mw onder zwaartekracht, q_v = −100 N/m | −41,67 | −49,48 | −50 |

V en N zijn per element constant en gelijk aan de waarde in het midden. M wijkt aan beide einden q·h²/12
af. Met lasten q_u, q_v, q_w in elementassen en element-coördinaat s (0..h) is dat exact te herstellen:

```
N(s)  = N_fem  + q_u·(h/2 − s)
Vv(s) = Vv_fem + q_v·(h/2 − s)                 Vw analoog met q_w
Mw(s) = Mw_fem(s) + q_v·(h²/12 − s·(h−s)/2)
Mv(s) = Mv_fem(s) − q_w·(h²/12 − s·(h−s)/2)    Mw_fem, Mv_fem lineair tussen de eindwaarden
```

Dit geldt voor **Mecway's eigen solver met line2**. Bij een solver die exacte eindwaarden geeft, zou de
term h²/12 juist een fout toevoegen; daar ontbreekt alleen de uitbuiging −q·s(h−s)/2. De tool bepaalt dat
per oplossing: V gelijk aan beide einden terwijl q ≠ 0 betekent middenwaarden, V(h) − V(0) ≈ −q·h betekent
eindwaarden. CCX line3 is gemengd (gemeten): N en V zijn eindwaarden, maar M aan de hoekknopen draagt wel
q·h²/12, dus daar geldt de volledige term voor M. Code-Aster (gemeten): `FORCE_POUTRE` geeft exacte
eindwaarden op line2 en line3, zwaartekracht ook op `POU_D_E` en `POU_D_T`, maar op `POU_D_TG` middenwaarden
met q·h²/12. Een mix van beide op `POU_D_TG` krijgt alleen een melding.

Hermite op eind-M en eind-V werkt niet op Mecway's waarden: V is aan beide einden gelijk, dus er zou stil
een rechte lijn uit komen. De lasten komen uit de `.liml`, in globale assen, en worden per element naar
lokale assen omgerekend en opgeteld. Lasten die de tool niet kent geven een waarschuwing. Formules in
x, y, z op lasten past Mecway 34 volgens eerdere metingen niet toe; ook dat geeft een waarschuwing.

### 5.5 Buiten de basis

- **Verhinderde welving.** Levert σ_ω = B·ω/Iω plus secundaire schuif. Mecway's balk kent geen welving,
  dus daar is B = 0 en dat staat op het scherm. `POU_D_TG` levert `BX`, gemeten met het teken
  `+E·JG·θ''`, dus tegengesteld aan Vlasov's B, en alleen bij GRX = 0 in de inklemming. Teken en waarde
  van σ_ω zijn NOT verified, want Code-Aster geeft die niet. De secundaire schuif vraagt één extra
  Poisson-oplossing ([Sapountzakis & Mokos 2003](https://doi.org/10.1007/s00466-002-0373-4)).
- **Plasticiteit, knik, grote rotaties, modes.** Buiten de lineaire theorie: een melding, of geschaald
  en als zodanig gemarkeerd.

### 5.6 Grens van de theorie bij open profielen

Gemeten op hex20-modellen: bij een open profiel klopt de spanning pas op enkele 1/k van een inklemming of
krachtinleiding. Een U van 8h door S gaf op midspan σ 2,7 % en τ 8,6 % fout; op 14h van de einden 0,009 %
en 0,36 %. De oorzaak is een restbimoment van de modelranden, geen fout in de tool: de verstoring dempt uit
met 2,37h, en 1/k = 2,38h. Het venster noemt daarom 1/k bij zo'n profiel.

### 5.7 Literatuur

- W.D. Pilkey (2002), *Analysis and Design of Elastic Beams: Computational Methods*, Wiley. Hoofdbron,
  ook van `section_fem.py`. Niet ingezien; formules nagerekend via code en proefje.
- F. Gruttmann, R. Sauer, W. Wagner (1998), [*Shear stresses in prismatic beams with arbitrary cross-sections*](https://www.ibs.kit.edu/download/Mit_1998-01(1).pdf).
- F. Gruttmann, W. Wagner (2001), [*Shear correction factors in Timoshenko's beam theory for arbitrary shaped cross-sections*](https://www.ibs.kit.edu/download/Mit_2001-06.pdf).
- S.B. Dong, C. Alpdogan, E. Taciroglu (2010), *Much ado about shear correction factors in Timoshenko
  beam theory*, IJSS 47(13), [doi:10.1016/j.ijsolstr.2010.02.018](https://doi.org/10.1016/j.ijsolstr.2010.02.018).
- E.J. Sapountzakis, V.G. Mokos (2003), [*Warping shear stresses in nonuniform torsion by BEM*](https://doi.org/10.1007/s00466-002-0373-4),
  Computational Mechanics. Secundaire welvingsfunctie, nodig voor F6.
- [US DOT-rapport](https://rosap.ntl.bts.gov/view/dot/18908/dot_18908_DS1.pdf): verhinderde welving met
  eindige elementen. Titel en auteurs nog na te lezen.
- J.J. Connor, [hoofdstuk 13 (MIT)](https://www.ocw.mit.edu/courses/1-561-motion-based-design-fall-2003/07ffc2f96f70b107e632b5597089a5dd_connor_ch13.pdf): bimoment.
- Code-Aster [R3.08.04](https://ericca.uqtr.ca/fr14.4/man_r/r3/r3.08.04.pdf) (`POU_D_TG`), U4.42.01, U2.01.05.
- Uit eigen kennis, niet nagezocht: Vlasov (1961), Kollbrunner & Basler (1969), Timoshenko & Goodier
  (1970), Cowper (1966), Williams (1952).

## 6. Voorgestelde aanpak

### Wie rekent wat

| Taak | Waar |
|---|---|
| Snedekrachten | Solver: Mecway intern, CCX, of Code-Aster via de bridge. Geen wijziging voor de basis |
| Vorm en eigenschappen | `SECTIE-VORM` of Mecway's standaardprofielen (afmetingen uit de `.liml`) |
| Eenheidsvelden en spanningen | **Deze tool**, in CPython met numpy en scipy |
| Weergave | Deze tool |
| Bimoment (later) | Bridge exporteert `BX`, tool gebruikt het |

Antwoord op de vraag "tool of bridge": de tool. Hij werkt dan ook zonder Code-Aster, en dezelfde kern kan
later de bridge en ParaView bedienen.

### Opbouw

```
Mecway ─ IronPython-paneel (WinForms op eigen STA-thread, mw-aanroepen via één thread)
          │  selecties, keten, schuifbalk, vinkjes, dummy-element
          │  verzoeken met volgnummer; alleen het nieuwste verzoek wordt uitgevoerd
          ▼
        CPython-werker (blijft draaien, numpy + scipy)
          - doorsnede: mesh → ω, ψ, φ → eenheidsvelden, in cache per (vorm, ν)
          - optellen, kleuren en rasteren tot een beeld
```

Waarom zo: IronPython heeft geen numpy, en tekenen met `FillPolygon` kostte 155 ms per beeld met vlakke
kleuren. Metingen aan een echt beeld: §6.1.

**Eigenschappen.** σ rekent met A, Ivv, Iww en Ivw van de solver, zodat de spanning past bij het
evenwicht dat de solver gebruikte. De τ-velden komen uit het mesh en zijn genormeerd op eenheidsresultante,
zodat ∫τ dA = V en het moment = T per constructie gelden. Wijken de twee bronnen af, dan staat dat op het
scherm, want dan past de `SECTIE-VORM` niet bij het materiaal.

Regels voor de werker, uit de review:

- Eén werker per paneel; hij stopt bij het sluiten van stdin en wordt in `finally` afgesloten.
- Het paneel wacht nooit blokkerend op de werker: een rekenopdracht van 1,6 s mag Mecway niet stilzetten.
- Schuifbalk-gebeurtenissen worden samengevoegd.
- Rasteren op echte pixels, zodat het beeld scherp blijft bij 125 en 150 % schaal.
- Python zoeken zoals `section_utils.find_python`: eerst Code-Aster, nooit pip in die Python.
- Pijp via `System.Diagnostics.Process`, niet via `subprocess.Popen` (§6.1).
- Afhankelijkheden: alleen wat MecwaySectionProps al vraagt (Python 3, numpy, scipy, BSD-licenties).

### 6.1 Meting aan een echt beeld

Gemeten op een I-profiel met afrondingen, 29 296 knopen, met echte puntlokalisatie, kleuren, omtrek en
pijlen, in een gewoon venster buiten Mecway. Volledige ronde vanuit IronPython, in ms:

| Beeld | Rekenen | Pijp | Tekenen | Ronde, mediaan | Ronde, p95 | Nieuwe pixelkaart |
|---|---|---|---|---|---|---|
| 800 × 600, passend | 0,8 | 0,6 | 0,4 | 2,0 | 3,3 | 49 |
| 800 × 600, ingezoomd | 7,5 | 1,1 | 0,6 | 9,1 | 10,7 | 132 |
| 1456 × 1092, passend | 1,8 | 1,7 | 0,9 | 4,7 | 6,3 | n.g. |
| 1456 × 1092, ingezoomd | 23,7 | 2,7 | 0,9 | 27,5 | 29,3 | ±440 (op 1600 × 1200) |

- **Keuzes.** Pijp via `System.Diagnostics.Process` en `BaseStream`, tekenen met `SetDIBitsToDevice`.
- **Tekenen was de duurste stap.** `DrawImage` kost op het scherm 11 tot 40 ms, `SetDIBitsToDevice` 1 ms.
  De oude route kwam daardoor op 13 tot 67 ms per ronde.
- **Pijp volstaat.** Gedeeld geheugen scheelt 1 tot 2 ms en vraagt een naam, opruimen en een vaste
  maximumgrootte. IronPython's `subprocess.Popen` valt af: 20 ms extra en veel opruimwerk. Beeldbytes zijn
  in beide richtingen op MD5 gecontroleerd.
- **Samenvoegen** bij 60 stappen per seconde: de vertraging loopt niet op. Mediaan 5 tot 11 ms, in het
  zwaarste geval 38 ms met 44 % vervallen stappen; de laatste stap wordt altijd getekend. Dit is gemeten
  zonder zichtbaar venster, want het venster was geminimaliseerd tijdens de meting.
- **Zoomen** vraagt een nieuwe pixelkaart van 50 tot 440 ms, dus daar is een grove tussenweergave nodig.
- Eenmalig bij een doorsnede: mesh en FEM 0,1 tot 0,5 s. Koude start van de werker: 1,4 s.

**Overgeslagen of herhaald.** De eerste drie gevallen hadden alle blokken geldig. In het vierde is het
venster geminimaliseerd: 4 blokken geldig, 6 overgeslagen, drie herhaalrondes mislukt, dus 190 beelden in
plaats van 490. De vier metingen van het samenvoegen in het venster zijn overgeslagen.

**NOT verified.** Alles binnen Mecway zelf, samenvoegen met een echte schuifbalk en echt tekenen, 1600 × 1200
op het scherm (paste niet in het werkgebied; schatting ±33 ms), detectie van een afgedekt venster, en
tearing op het scherm.

**Regels voor meetvensters.** Twee eerdere metingen openden een randloos venster van 1600 × 1200 op (0,0)
zonder taakbalkknop, en dat bedekte het scherm van de gebruiker. Vanaf nu: gewoon venster met rand en
taakbalkknop, niet `TopMost`, pakt de focus niet, één venster per meting, en een afgedekte meting wordt
gemarkeerd in plaats van naar voren gehaald. Dit hoort in de testafspraken van het project.

### Hergebruik

- `section_fem.py`: eerst een kleine wijziging in MecwaySectionProps, zodat ψ, φ en de elementmatrices
  naar buiten komen. Zonder die wijziging moet de kern privéfuncties aanroepen en de velden dubbel
  oplossen. Dit is de enige wijziging die het plan elders vraagt.
- Daarna `section_fem.py` en `section_mesh.py` ongewijzigd overnemen (MPL-2.0, zelfde auteur), vanaf een
  nieuwe release van MecwaySectionProps, met herkomst in de kop en een test die verschil met de zusterrepo
  meldt. `section_stress.py` bouwt erop voort.
- Patronen, niet de code: paneel en `_MwCaller` uit MecwaySectionProps, tijdelijke elementen uit
  `bridge_section_model.py`, base64-resultaten uit `bridge_results_liml.py`, assen uit
  `bridge_section_view.py` (`beam_frame`) en `bridge_warping.py` (`mecway_frame`, de gemeten assen van
  L en C).

### Gedrag in het scherm

- **Keuze:** lijst met element- én face-selecties, plus "huidige selectie". `FaceId.element_id` werkt op
  balken (gemeten). Geen beeld als de elementen geen doorlopende keten vormen.
- **Keten:** op volgorde via gedeelde knopen, positie = booglengte. Knik mag, want elke doorsnede rekent
  in het eigen element; wel een melding. Elementen tegen de richting in en wisselende doorsneden worden
  per element afgehandeld.
- **Resultaat:** stap, mode of configuratie kiezen. Element-knoopwaarden waar die er zijn.
- **Spanning:** keuzelijst (σ, τ, von Mises, Tresca, hoofdspanningen) en vinkjes per snedekracht
  N, Vv, Vw, T, Mv, Mw. Vectoren voor τ, als pijlen op een regelmatig raster.
- **Meerdere doorsneden:** rijen kleine beelden met één gedeelde kleurschaal. Een 3D-stapel is voor later.
- **Contourlijnen:** vinkje, op de niveaus van de legenda, bij één en bij meerdere doorsneden.
- **Positie:** standaard in het eigen venster, als klein zijaanzicht van de keten bij de schuifbalk. Dat
  werkt voor elke analyse en vraagt geen beeldwisseling in Mecway. Het dummy-element is een optie, zie §7 E1.
- **Terugval zonder snedekrachten:** alleen σ uit de kinematica; τ en von Mises ontbreken dan. Het venster
  zegt dat en noemt de vier regels die de gebruiker kan toevoegen.

### Verificatie

Marges vooraf vastgelegd. Plaats: minstens max(5h, 4/k) van beide einden, met 1/k = √(E·Iω / (G·J)). De
eerste eis, 2h, bleek voor open profielen te krap (gemeten, zie `tests/validation/RESULTS.md`). Velden buiten
de uitsluitingszone van §5.3.

| Nr | Toets | Marge |
|---|---|---|
| V1 | Exact: rechthoek (reeks voor wringing, 1,5 V/A bij ν = 0), cirkel en buis met vaste polygoon en mesh | 5e-3 op puntwaarden, 1e-6 op J en resultanten |
| V2 | Dunwandig: I, U, T: lijf op neutrale lijn, resultanten, schuifcentrum. De rest van het veld heeft geen exacte referentie | 1e-3 |
| V3 | Asymmetrisch profiel: de doorsnede draaien naar hoofdassen en daar met Ivw = 0 rekenen, zodat de toets niet op zichzelf steunt | 1e-6 |
| V4 | Eigen tekenmodellen: elk van de zes krachten, omgekeerde knoopvolgorde, V-vector met hoek | exact teken, 1e-6 |
| V5 | `stressptu` van Mecway op recovery-punten, met de eigenschappen van de solver | 1e-6 op σ |
| V6 | Volumemodel hex20: rechthoek, I, koker, L door G, U door S | σ 1 %, τ 5 % |
| V7 | T_S: L door G. U door G alleen als de balk ≥ 10/k lang is: een korte U is daar welvingsverhinderd (bij L = 5h is nog 36 % welvingswringing) en vergelijkt dan theorie in plaats van de tool | τ 5 % |
| V8 | Code-Aster-oplossing op dezelfde modellen, want in de bridge NOT verified | als V6 |
| V9 | Verdeelde last en zwaartekracht op line2, recht en schuin: uitkomst tegen evenwicht | 1e-6 |
| V10 | Dummy laat oplossing en bestand ongewijzigd; let ook op de Undo-lijst | byte-gelijk na opslaan |
| V11 | Eenheden: strings als `"mm⁴"` in de `.liml` tegen SI van de API | exact |
| V12 | Beeldsnelheid in Mecway zelf, want §6.1 is daarbuiten gemeten | mediaan onder 100 ms |
| V13 | Venster: gebruiker | oordeel gebruiker |

`sectionproperties` zou een goede kruiscontrole zijn, maar trekt Triangle mee. Daarom vaste
referentiewaarden en het volumemodel, tenzij de gebruiker anders beslist.

### Fasen

| Fase | Inhoud | Klaar als |
|---|---|---|
| F1 | Kleine wijziging en release MecwaySectionProps, kern `section_stress.py`, eenheidsvelden, hoekzones | V1, V2, V3 groen |
| F2 | Keten, assen, tekens, krachten lezen, T_S; standaardprofielen als polygoon; verdeelde last: detectie, correctie Mecway line2; waarschuwing CCX line2 | V4 t/m V7, V9, V11 groen |
| F3 | Venster: één doorsnede, schuifbalk, kleur, vectoren, vinkjes, zijaanzicht met positie | V12, V13 |
| F4 | Meerdere doorsneden, optie dummy-element | V10, V13 |
| F5 | Code-Aster-oplossingen, documentatie NL/EN, eerste release | V8 groen |
| F6 (optie) | Bimoment via `BX`, secundaire schuif, sidecar voor bridge en ParaView | Apart besluit |

Elk onderdeel krijgt tests en een onafhankelijke code-review door Fable voordat het als klaar geldt.

## 7. Uitkomsten van de proeven in Mecway

Gedaan vóór het plan, want de gebruiker gaf ruimte. Eigen model met tien balken, elk één belasting.

| Nr | Vraag | Uitkomst |
|---|---|---|
| E1 | Is een API-element zichtbaar bij de resultaten? | **Alleen in het modelbeeld.** In het oplossingsbeeld staan 41 elementen, in het modelbeeld 42, en de opname bevestigt het. De oplossing blijft geldig (waarden gelijk voor, tijdens, na). Verplaatsen met `set_node_x` kost 0,2 ms, met `mw.display()` 100 ms op dit kleine model |
| E2 | Tekens van de zes snedekrachten | Gelijk aan Code-Aster: tip +Y geeft `sheary` +F en `bendingmomw` +F(L−x); tip +Z geeft `shearz` +F en `bendingmomv` −F(L−x); +X-koppel geeft `torsion` +. Ook goed bij omgekeerde knoopvolgorde en bij V = +Z. σ uit deze krachten = `stressptu` |
| E3 | Verdeelde last, zwaartekracht, schuine balk | Zie §5.4: zelfde patroon op alle vier |
| E4 | CCX | Geen element-knoopwaarden, wel knoopwaarden met dezelfde tekens. Line3: M en V goed. **Line2: M 86 in plaats van 100**, dus line2 met CCX krijgt een waarschuwing |
| E5 | Mystran | Alleen verplaatsing en rotatie: terugval op de kinematica uit `bridge_section_view.py` |
| E6 | Welke velden worden opgeslagen? | Alleen met `<fieldvariable>` `tensile force`, `shear force`, `torsion moment` en **`bending moment 2`**. Zonder die regels zijn er geen krachten en vallen we terug op de kinematica, die alleen σ geeft. Een fout veldnaam of eenheid geeft een modale melding, dus de tool schrijft deze regels nooit zelf |
| E7 | Index van `element_node_value` | 1-gebaseerd, in elementassen. Index 0 en 3 geven een fout |
| E8 | Face-selectie op balken | `named_selection` geeft `FaceId` met `element_id`, op balken bruikbaar |
| E9 | Oriëntatie | Geen API. De tool leest die uit de opgeslagen `.liml`, dus het model moet opgeslagen zijn |
| E10 | Nieuwe variabelen in een oplossing | `solution.set_variable` neemt een nieuwe naam, alleen per knoop, laat andere velden staan en blijft na opslaan en heropenen. Basis voor de knop *Schrijf naar Mecway-resultaten* |
| E11 | Marker bij oplossen | Statisch intern en CCX: krachten gelijk op 2e-13. Modaal: lege oplossing zonder foutmelding, dus het venster waarschuwt |
| E12 | Code-Aster, V8 | 13 modellen tegen statica op 1e-9, T_S op 1e-7, 48 rijen tegen hex20: σ 0,18 %, τ 3,82 %. V2026.8 volstaat voor de tool; `6efd62a` raakt alleen de reacties |

**Gevolg van E1 voor het venster.** De wens "positie zien" kan niet terwijl Mecway de kleuren toont. In het
oplossingsbeeld bijtekenen vraagt `mw.open`, en dat sluit het paneel. `solution.set_variable` overschrijft
alleen bestaande knoopwaarden en `set_active_configuration` wisselt een oplossing; geen van beide is een
markering. Daarom staat de positie standaard in het eigen venster. Het dummy-element wordt een vinkje *Toon
positie in Mecway*: dat schakelt naar het modelbeeld, verplaatst het element bij loslaten van de schuifbalk
(`mw.display()` duurt tot 0,5 s op grote modellen), en ruimt bij sluiten alles op en schakelt terug.

**Handig voor tests.** Een script dat `mecway.exe model script x.py` draait kan het verborgen venster
tonen met `Control.FromHandle(...).Show()`. Dan werkt de 3D-weergave en kunnen GUI-proeven zonder klikken.

## 8. Wat nog openstaat

- Oordeel van de gebruiker op het echte venster; dat kan alleen op het oog.
- CCX line2 onder verdeelde last: alleen een waarschuwing.
- De hoekregel van §5.3 op andere vormen.
- Mecway 34: niet getoetst, en deze PC heeft alleen 35.
- Andere analyses dan Static 3D voor het dummy-element.
- Teken en waarde van σ_ω uit `BX`: F6.
- Pilkey's boek inzien vóór F6.

## 9. Gevolgen voor andere stappenplannen

Nog niets veranderen; dit zijn voorstellen voor het moment dat de gebruiker het plan goedkeurt.

- **Bridge S41:** vult deze tool in voor Mecway. Voor ParaView blijft een deel open: de kern kan een
  sidecar met velden schrijven. `longitudinalstress` blijft zoals het is.
- **Bridge, klein:** `BX` exporteren in F6; de Code-Aster-route van `beam_forces` toetsen in F5.
- **Bridge, nieuw:** bij de Code-Aster-meting bleek dat `SIEF_ELGA` en `REAC_NODA` van `POU_D_TG` in
  `MECA_STATIQUE` fout zijn onder dwarskracht (reactie −3969 tegen −1000), terwijl `EFGE_ELNO` en de
  verplaatsingen goed zijn. De invloed op de bridge is niet nagegaan. Dat is een eigen taak.
- **De aparte nabewerkingstool (§3):** dezelfde kern kan het ontbreken van generieke doorsneden oplossen. De
  filter moet T_S gaan gebruiken bij een excentrisch schuifcentrum.
- **MecwaySectionProps:** ψ en φ teruggeven (F1). Op termijn afrondingen in `SECTIE-VORM` bewaren.

## 10. Licentie en juridische punten

- Eigen code: voorstel MPL-2.0, net als de zustermappen; overgenomen bestanden moeten dat toch houden.
- numpy, scipy: BSD. Code-Aster draait als apart programma; de tool leest alleen resultaten.
- Geen code uit AGPL- of LGPL-projecten, geen Triangle. Bij AGPL-code alleen de theorie volgen, niet de
  code ernaast leggen.
- Mecway: alleen API-namen, geen tekst uit handleiding of API-referentie.
- Aandachtspunt: `sectionproperties` is eerder als referentie gebruikt bij MecwaySectionProps. De code is
  MIT en het gebruik was vergelijken, geen overname. Maar die installatie trekt Triangle mee, en of dat
  binnen een bedrijf onder "institutional use" valt is niet zeker.

## 11. Review

Fable heeft de eerste versie onafhankelijk beoordeeld. Oordeel: *eerst aanpassen, dan door*. Verwerkt:

| Bevinding | Ernst | Wat ermee gedaan is |
|---|---|---|
| Groepering schuifcentrum per solver klopte niet, `e` en teken ongedefinieerd | Blokkerend | Gemeten in Code-Aster, formule met teken in §5.2 |
| De schermcontrole bewijst tekens niet | Belangrijk | Herschreven; tekens in E2 en V4, V5 |
| Formule alleen in hoofdassen | Belangrijk | Algemene vorm met Ivw |
| Hermite faalt op Mecway's elementwaarden | Belangrijk | Gemeten, evenwichtsformule in §5.4, waarschuwing naar F2 |
| De 25 ms van het proefje was niet representatief | Belangrijk | Echte meting in §6.1 |
| Gedeeld geheugen voorbarig, levensduur en blokkeren niet ontworpen | Belangrijk | Gemeten: pijp volstaat (§6.1). Regels voor de werker in §6 |
| Ongewijzigd kopiëren dwong tot privéfuncties | Belangrijk | Kleine wijziging in MecwaySectionProps eerst |
| Marges ontbraken, tekentest te smal | Belangrijk | Verificatietabel |
| F0 was onvolledig | Belangrijk | Uitgevoerd, §7 |
| Geen criterium voor hoeken | Klein | Gemeten, §5.3 |
| Triangle-bron | Klein | Primaire bron gebruikt |

De tweede review op de bijgewerkte versie vond geen blokkerende fout: σ, τ, T_S en de correctie zijn
zelfstandig herleid en kloppen met de metingen. Oordeel: *kan naar de gebruiker na verwerking van de
belangrijke punten*. Een derde ronde is niet nodig. Verwerkt:

| Bevinding | Ernst | Wat ermee gedaan is |
|---|---|---|
| Straal 0,5 t was te krap, "t" dubbelzinnig, "stabiel" te stellig | Belangrijk | §5.3 herschreven, straal 1 t, gedefinieerd, als vuistregel |
| Correctie is specifiek voor Mecway line2; N en zwaartekracht ontbraken | Belangrijk | §5.4 herschreven, zwaartekracht, q langs W en schuine balk gemeten, detectieregel |
| V1 te streng, U en T hebben geen exacte oplossing | Belangrijk | V1 en V2 per grootheid |
| U door G niet toetsbaar zonder welving | Belangrijk | V6 en V7 gesplitst |
| Keuze van eigenschappen onbeslist | Belangrijk | Regel in §6, en kanttekening in §5.2 |
| Conclusie over 2e-5 te stellig | Belangrijk | §1 afgezwakt |
| §6.1 niet in Mecway gemeten, koude start ontbrak | Klein | Vermeld, V12 toegevoegd |
| Beter alternatief voor de positie | Klein | Standaard in het eigen venster, dummy als optie |
| Terugval zonder krachten geeft geen τ | Klein | Vermeld in §6 en E6 |
| Standaardprofielen, CCX line2, V2 circulair, release MecwaySectionProps | Klein | In F1, F2 en V3 opgenomen |
| GitHub-namen van personen in links | Klein | Vervangen door pakketlinks |

## 12. Besluiten van de gebruiker (7 november 2026)

| Vraag | Besluit |
|---|---|
| Licentie | MPL-2.0 |
| MecwaySectionProps | Kleine wijziging voor ψ en φ, met een nieuwe release |
| Eerste release | F1 t/m F5; F6 is een apart besluit later |
| Auteursnamen | Toegestaan in bronvermeldingen, ook in codekoppen. Verder geen persoonsnamen |
