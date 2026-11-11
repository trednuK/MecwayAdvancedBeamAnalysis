# Stand van het werk (11 november 2026, V2026.2 uitgebracht)

- Uitgebracht als V2026.2 op 11 november 2026: de 3D-weergave volgt Mecway's keuze voor de as omhoog
  (*Tools* > *Options* > *General*), gelezen uit Mecway's opgeslagen instellingen; gemeten op Mecway 35. Niet gemeten:
  wanneer Mecway een gewijzigde keuze opslaat.
- Uitgebracht als V2026.1 op 10 november 2026: rekenkern, validatie, venster met 2D, meerdere doorsneden, 3D en
  detail, schrijven naar Mecway-resultaten, NL/ENG, handleiding op het tweede tabblad; inhoud in `CHANGELOG.md`. Versie
  in `beam_utils.VERSION`, in de venstertitel en in `beam_log.txt`.
- Samen uitgebracht op 10 november 2026: MecwaySectionProps V2026.4 en MecwayAsterBridge V2026.9. Deze tool vraagt
  MecwaySectionProps V2026.4 of nieuwer voor algemene doorsneden met vorm, en de bridge is optioneel: V2026.9 of
  nieuwer voor Code-Aster-oplossingen, ook voor `POU_D_TG` en de spanningen van de bridge als Mecway-resultaten (S41).
- `#ASTER-SECTIE` telt alleen als A, IY, IZ en JX bij de doorsnede passen, zoals in MecwayAsterBridge.
- `tests/test_section_copies.py` vergelijkt `section_fem.py` en `section_mesh.py` met `main` van MecwaySectionProps.
- Door de gebruiker bekeken in Mecway: venster, 3D en detail met draaien en wiel, NL/ENG, handleiding,
  3D-standaardstand, schrijfknop.
- Nog niet door de gebruiker bekeken: gespiegelde doorsneden bij omgekeerde elementen, meldingen `section_direction` en
  `shape_inertia`, de 3D-weergave met Z omhoog.
- Bekend: bij dunwandige profielen en veel doorsneden is de automatische vergroting 1×; de schuif vergroot verder.
- Tests: op een verse checkout slaan de delen die `tests/mecway/results/` nodig hebben over.
