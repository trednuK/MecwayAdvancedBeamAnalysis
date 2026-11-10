# Validatie V6 en V7: rekenkern tegen volumemodellen

Getoetst: `Scripts/section_stress.py` op V2026.1 (de spanningsformules zijn sinds de meting niet veranderd), tegen eigen hex20-modellen in Code-Aster 17.4.
Alle getallen zijn gemeten, tenzij er "NOT verified" of "voorstel" staat.

## Conclusie

- **V6 groen op σ, τ en von Mises, mits de vergelijking ver genoeg van de einden ligt.** Grootste fout:
  σ 0,18 %, τ 3,82 % (marge 1 % en 5 %).
- **V7 groen.** Met T_S is de τ-fout hoogstens 3,54 %, met kale T minstens 62,8 % (eis: ≥ 25 %).
- **De eis "minstens 2h van de einden" is voor open profielen te krap.** Op midspan van een U van 8h
  faalt de marge (σ 2,71 %, τ 8,57 %). Dat is geen fout in de tool, maar een bimoment van het model
  (zie "Oorzaak U ZS"). De marges hoeven niet omhoog; de plaatsregel moet anders.
- **Geen defect gevonden in `section_stress.py`.** Wel twee aandachtspunten voor het model van de
  solid zelf: zie "Valkuilen van de solid".

## Opzet

- Cantilever langs X, V = Y, W = Z. E = 210 GPa, ν = 0,3 (rechthoek ook ν = 0).
- Eigen gestructureerd hex20-mesh, 27 Gauss-punten per element (gemeten). Eind x = 0 ingeklemd, eind
  x = L alleen traction, dus vrij om te welven: N, Y en Z uniform, T lineair in het vlak, om G.
- U door S = geval Z + e·T, met e uit de tool. T_S is daar gemeten 5e-7 × V·e.
- Snedekrachten geïntegreerd uit het volumemodel; die gaan de tool in. Het evenwicht klopt op 2e-6 of
  beter.
- Punten: de Gauss-punten van de doorsnede, 9 per element, tool geïnterpoleerd met tri6-vormfuncties
  (`n_target` 12 000).
- Maat, vooraf vastgelegd: grootste fout gedeeld door de piek. τxy en τxz gaan op de piek van de τ-vector.
  Buiten zones r = 1t, tenzij anders vermeld.
- Secties (mm): rechthoek 40×80; I 100×60, tf 8, tw 6; koker 100×60×6; L 80×50×8; U 100×50, tf 8, tw 6.

## V6: stress op midspan

Fijnste mesh, gewone traction. L door G heeft assen langs de benen, dus Ivw ≠ 0.

| Sectie, geval | Model | σ (1 %) | τxy | τxz | τ-vector (5 %) | von Mises (5 %) | Uitslag |
|---|---|---|---|---|---|---|---|
| Rechthoek N+Y+Z+T | 8h, n4 | 0,000 | 0,61 | 0,55 | 0,82 | 0,01 | OK |
| Rechthoek T | 8h, n4 | n.v.t. | 0,81 | 0,82 | 1,09 | 1,09 | OK |
| Rechthoek Y (ν-gevoelig) | 8h, n4 | 0,000 | 0,52 | 0,48 | 0,67 | 0,01 | OK |
| I N+Y+Z | 16h, c4 | 0,000 | 0,45 | 0,18 | 0,47 | 0,01 | OK |
| Koker N+Y+Z+T | 16h, c4 | 0,004 | 0,33 | 0,25 | 0,33 | 0,02 | OK |
| Koker T | 16h, c4 | n.v.t. | 0,57 | 0,57 | 0,81 | 0,81 | OK |
| L door G, N+Y+Z+T | 8h, c6 | 0,005 | 0,97 | 0,91 | 1,24 | 0,03 | OK |
| L door G, Y | 8h, c6 | 0,004 | 1,58 | 1,40 | 2,00 | 0,17 | OK |
| L, T | 8h, c6 | n.v.t. | 2,01 | 1,91 | 2,72 | 2,72 | OK |
| U door S (Z + e·T) | 28,6h, c4 | 0,009 | 0,36 | 0,21 | 0,36 | 0,01 | OK |
| U door S, Y | 28,6h, c4 | 0,000 | 0,28 | 0,22 | 0,29 | 0,00 | OK |
| U, T, ver van de einden | 28,6h, c4 | n.v.t. | 2,79 | 2,61 | 3,82 | 3,82 | OK |

n4 = 4 elementen door de wand, gelijkmatig. c4/c6 = idem, verdicht naar de wandeinden. "n.v.t.": onder
zuivere T is de σ-piek nul. Per τ-component op zijn eigen piek: combinaties ≤ 1,08 %, kleinste component
in een enkel geval ≤ 3,77 %.

Dezelfde modellen op midspan van de korte balk, dus 4h van de einden:

| Sectie, geval | σ | τ | Uitslag |
|---|---|---|---|
| I Y, 8h | 0,02 | 4,66 | binnen de marge, maar eindeffect |
| Koker T, 8h | n.v.t. | 4,77 | binnen de marge, maar σyy, σzz ≈ 13 % van von Mises |
| **U door S, 8h** | **2,71** | **8,57** | **FAIL, oorzaak hieronder** |
| U door S, 8h, Saint-Venant-traction | 0,40 | 2,16 | OK, zie hieronder |

ν-toets, rechthoek: de solid wijkt bij Y 44,6 % af van de parabool 1,5 V/A, de tool volgt binnen 0,67 %.
Met ν = 0 volgt de solid de parabool binnen 0,13 % en de tool binnen 0,16 %.

## V7: T_S tegen kale T

Eis vooraf: T_S ≤ 5 % én kale T ≥ 25 % (τ-vector). Alle lasten door G.

| Model | Last | T_S-term (N·m per kN) | τ met T_S | σ | τ met kale T | von Mises met kale T | Uitslag |
|---|---|---|---|---|---|---|---|
| L, 8h, c6, kL = 20,9 | Z | +8,70 | 1,72 | 0,01 | 62,8 | 15,9 | OK |
| L | Y (toetst −EZ·Vv) | −22,5 | 2,00 | 0,00 | 75,1 | 19,0 | OK |
| L | Y+Z | −13,8 | 1,68 | 0,00 | 64,2 | 9,8 | OK |
| U, 28,6h, c4, kL = 12,0 | Z | +31,9 | 3,54 | 0,18 | 92,7 | 35,1 | OK |

- k: U 4,20 /m, dus 10/k = 2,38 m, gebouwd 2,86 m. L 32,7 /m, dus 10/k = 0,31 m.
- Vlasov-rest bij U: T_ω/T = 0,25 % op midspan. Gemeten bimoment-coëfficiënt is 1,09 × de formule; die
  rest geeft 0,17 % van σ. Dat de balk ≥ 10/k moet zijn, klopt dus: op 2,3h van de klem is de σ-fout
  nog 13 %.

**S-toets, los van de spanning.** De spanning van de tool hangt voor gegeven V en T om G niet af van welke
S je kiest, want S verschuift veld en term samen. S is daarom apart gemeten, op de lange U:

| Bron | y_S − y_G (mm) |
|---|---|
| Solid, nulpunt van de wringsnelheid | −31,928 |
| Tool, elastisch (`shear_centre`) | −31,932 |
| Trefftz | −31,935 |
| Fit op het τ-veld (arm) | −31,871 |

Het verschil tussen elastisch en Trefftz is hier 0,002 mm, kleiner dan de meetfout. Welke definitie juist
is, is dus niet onderscheiden.

## Oorzaak U ZS (2,66 % op σ)

Antwoord: **een restbimoment van de modelranden**. Niet het mesh, niet T_S, niet `section_stress.py`.

| Verklaring | Meting | Oordeel |
|---|---|---|
| Mesh | n = 2, 3, 4: 2,66 / 2,69 / 2,71 %; dx/2: 2,690 tegen 2,689 % | Uitgesloten |
| T_S | T_S / (V·e) = −3e-9 op de korte balk; S klopt op 0,005 mm | Uitgesloten |
| `section_stress.py` | Zelfde last, 14h van beide einden: σ 0,009 %, τ 0,36 % | Uitgesloten |
| Restbimoment | ω-fit uit de fout halen: 2,69 % wordt 0,19 %, dus 93 % is zuiver ω-vormig | **Bevestigd** |

Het bimoment heeft twee bronnen, die apart gemeten zijn:

- **Belast einde** (hoofdbijdrage op 8h). Een uniforme traction is niet de Saint-Venant-traction.
  Het verschil tussen die twee dempt uit met **2,37h, en 1/k = 2,38h**. Het is dus de langzame
  Vlasov-mode, niet een lokale distorsie, en de afstand schaalt met 1/k in plaats van met h. Met de
  Saint-Venant-traction op het einde zakt σ op 8h van 2,69 naar 0,40 %.
- **Klem** (rest op 8h). De klem verhindert de welving van de dwarskracht. c / (sinh k(L−x) / sinh kL)
  is constant langs de balk: 0,147 tot 0,151 op 8h tussen 2,5h en 5h, 0,186 tot 0,193 op de lange balk
  tussen 2h en 18h (eenheid 1e9 N/m⁴ per kN). Dat is de vorm uit de Vlasov-theorie. Waarom de constante op
  8h 20 % lager is dan op de lange balk: NOT verified.

De review ging ervan uit dat schuiftraction op het einde geen langzame mode opwekt. Dat is hiermee
weerlegd.

## Voorstel voor de verificatietabel

De marges blijven. De plaatsregel wordt:

> Vergelijken op minstens **max(5h, 4/k)** van beide einden. Voor V7 blijft L ≥ 10/k.

Onderbouwing, gemeten afstand tot het einde waarop de marge gehaald is (gewone traction):

| Geval | Belast einde | Klem | 1/k |
|---|---|---|---|
| I, Y | 3,7h voor τ 5 % | 3,7h voor τ 5 % | geen langzame mode, symmetrisch |
| Koker, T | 4,1h voor τ 5 %, 6h voor σyy, σzz < 1 % | 1,1h voor τ 5 %, 4h voor σyy, σzz < 1 % | n.v.t. |
| L, Y | 1,5h voor τ, 1,8h voor σ | 1,5h | 0,38h |
| L, T | 1,1h | 1,5h | 0,38h |
| U door S | 6h voor σ 1 %, 5,2h voor τ 5 % | < 2,3h | 2,38h |
| U door G | 6,3h voor σ 1 % | 9,3h voor σ 1 %, 7,5h voor τ 5 % | 2,38h |

Gevolg voor gebruikers, voorstel: de stress van de tool geldt bij een open profiel pas op enkele 1/k van
een inklemming of krachtinleiding. Dat raakt de tekst in het venster en de handleiding.

## Mesh-convergentie

Regel vooraf: binnen de marge op het fijnste mesh, en verandering tussen de twee fijnste < marge/2.

| Model | Mesh (vrijheidsgraden) | τ | σ | Verandering τ | Uitslag |
|---|---|---|---|---|---|
| Rechthoek combo | n2, 3, 4 (144k, 309k, 538k) | 1,73 / 1,20 / 0,82 | 0,000 | 0,38 | OK |
| I combo | c3, c4 (240k, 428k) | 0,54 / 0,47 | 0,000 | 0,08 | OK |
| Koker T | c3, c4 (294k, 503k) | 1,05 / 0,81 | n.v.t. | 0,24 | OK |
| L T | c4, c6 (157k, 329k) | 4,39 / 2,72 | n.v.t. | 1,67 | OK |
| U door S | c3, c4 (373k, 683k) | 0,66 / 0,36 | 0,009 | 0,29 | OK |
| U Z door G (V7) | c3, c4 | 5,05 / 3,54 | 0,18 | 1,51 | OK |
| U T | c3, c4 | 5,45 / 3,82 | n.v.t. | 1,62 | OK |

- De stress op vaste punten verandert tussen de twee fijnste meshes hoogstens 0,001 MPa op σ en 0,005 MPa
  op τ, bij pieken van 120 en 4 MPa.
- Tool-mesh: 3 000 / 12 000 / 48 000 elementen geeft combinaties binnen 0,15 %. Alleen U Z door G varieert:
  2,88 / 3,54 / 3,01 %, omdat de grootste fout op één punt naast een wandeinde zit.
- Op L T en U T daalt de fout nog met het mesh: die getallen zijn een bovengrens, beperkt door de solid.

## Hoekzones (§5.3)

Fout in de τ-vector buiten de zone, fijnste mesh:

| Geval | r = 1t | r = 2t |
|---|---|---|
| I Z | 0,45 | 0,07 |
| I Y | 0,28 | 0,33 |
| Koker T | 0,81 | 0,15 |
| L T | 2,72 | 2,32 |
| U Z door G | 3,54 | 2,28 |
| U T | 3,82 | 2,43 |

Met r = 1t blijft alles binnen 5 %, ook op U, L en koker. De regel van §5.3 houdt daar dus stand, op
scherpe hoeken. De restfout op L en U zit niet bij de inspringende hoek maar in de **bolle** hoeken, op
0,2 mm van twee randen (L: hiel en been-einde; U: buitenhoek en flens-einde). Daar daalt ze met het
solid-mesh en niet met het tool-mesh, dus de solid is de beperking. σ hangt niet af van de zone.

## Valkuilen van de solid

Geen van beide komt van de tool, maar ze vervormen de vergelijking als je ze mist.

1. **τ op het middenvlak van een laag is systematisch te laag.** De doorbuiging is kubisch in x en hex20
   kwadratisch. Daardoor mist op het middenvlak een fout van v'''·dx²/24: V is 0,74 % te laag, ongeacht het
   mesh in de doorsnede, en bij dx/2 nog 0,19 %. Het gemiddelde over de 3 Gauss-vlakken van de laag heeft
   die fout niet: V klopt op 1e-6, en dx/2 geeft dezelfde uitkomst (4,77 tegen 4,77 %). Daarom is overal
   dat gemiddelde gebruikt.
2. **Eén element over de eindzone van een wand is te weinig.** Op de L gaf gelijkmatig mesh n3 8,9 % op T, in de bolle hoeken.
   Het tool-mesh was daar al geconvergeerd. Verdichten naar de wandeinden gaf 2,7 %.

Solver: MUMPS weigerde de lange U op RESI_RELA 1e-6 (gemeten 4,0e-6), een gevolg van de slankheid. Op
de lange modellen staat `RESI_RELA` op 1e-4, met iterative refinement. Het evenwicht op de snedes blijft
2e-6 of beter.

Harnas: patchtest met minimale oplegging geeft σxx binnen 5e-12 van N/A, overige componenten 3e-13.

## NOT verified

- Toewijzing en tekens van echte solver-uitvoer aan de tool: hier zijn de krachten uit het volumemodel
  geïntegreerd (V4, V5, V8).
- Offset uit `#ASTER-SECTIE` die afwijkt van de tool.
- S-definitie, elastisch tegen Trefftz: op deze U niet te onderscheiden.
- Statisch onbepaalde constructies en de koppeling buiging-wringing in balkelementen.
- Korte open profielen onder T_S (L < 10/k) en de welvingsnormaalspanning: de tool rekent die niet.
- Wringing van de I: de I is alleen zonder T getoetst.
- Andere ν dan 0 en 0,3.
- Afgeronde hoeken en de fillet-regel: alle modellen hebben scherpe hoeken.
- Hoekzone: alleen op deze vijf secties.
- Doorsneden uit meerdere delen, verdeelde last (§5.4), niet-prismatische of schuine balken,
  `SECTIE-VORM`-afronding, `mesh_sensitivity`.
- Waarden binnen de zones en op de rand zelf: de punten liggen op Gauss-punten, tot 0,11 element van de rand.
- De Saint-Venant-traction van "U door S, 8h" komt uit de tool. Die regel is dus niet onafhankelijk; hij
  dient alleen om het belaste einde van de klem te scheiden.

## Opnieuw draaien

Met de Python van Code-Aster 17.4, vanuit `tests/validation/`. Uitvoer komt in `work/`, dat buiten git blijft.

```
python solid.py rect 2 --tag patch --patch --layers-h 3 && python patch_test.py
python solid.py rect 2|3|4                      # rect_n2, 3, 4
python solid.py rect 4 --nu 0 --tag rect_nu0_n4
python solid.py i 3 --cos --len-h 16 --layers-h 5 --stride-h 1 --resi 1e-4 --tag i16_c3      # ook 4
python solid.py box 3 --cos --len-h 16 --layers-h 5 --stride-h 1 --resi 1e-4 --tag box16_c3  # ook 4
python solid.py l 4 --cos --tag l_c4                                                         # ook 6
python solid.py u 2|3|4                         # korte U
python solid.py u 3 --sv --tag u_sv_n3
python solid.py u 3 --cos --sv --len 2.86 --layers-h 5 --stride-h 2 --resi 1e-4 --tag ulong_c3  # ook 4
python solid.py i 3 --layers-h 20 --tag i_n3_dx2
python solid.py u 3 --layers-h 20 --sv --tag u_n3_dx2

python summary.py rect_n4 i16_c4 box16_c4 l_c6 u_n4 ulong_c4:ulong
python convergence.py l l_c4 l_c6
python u_diag.py u_sv_n3 ulong_c4
python scan.py ulong_c4:ulong ZS Z
python dx_check.py i_n3 i_n3_dx2 u_n3 u_n3_dx2
python v7.py l_c6 ulong_c4
```

## Kanttekeningen uit de onafhankelijke review

Alle getallen hierboven zijn herberekend uit `work/*/result.npz` en kloppen. Wel:

- **Plaatsregel.** De regel is 4/k en niet 3/k: U door G heeft op de klem 3,9/k nodig voor σ 1 %.
- **Maat.** De fouten zijn grootste afwijking gedeeld door de piek. Puntsgewijs, op punten boven 30 % van de
  piek, zijn ze groter: U T 6,4 %, L T 4,6 %, L Y 4,1 %, U Z 3,7 %. "τ ≤ 5 %" geldt dus niet per punt.
- **ν-toets.** De 44,6 % is de solid-piek ten opzichte van 1,5 V/A; ten opzichte van de solid-piek is het 38,9 %.
- **`final.txt`.** De kolommen noBim en bim zijn daar verouderd; `diag.txt` is de bron van "2,69 wordt 0,19".
  De maat `bim` is ruw en is geen σ_ω.
- **Schuifcentrum.** De tool gebruikt de elastische S, die per constructie de arm van het eigen schuifveld is.
  Trefftz zou τ met τ_T·Δe·V verschuiven; hier is Δe 0,002 mm en dus onmeetbaar.
- **Von Mises** in de tabellen is √(σxx² + 3τ²) van de solid. Op de gerapporteerde plaatsen is dat gelijk aan de
  volledige von Mises.
