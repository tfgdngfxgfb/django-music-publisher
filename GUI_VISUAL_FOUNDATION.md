# Visuelt fundament for arbeidsgrensesnittet

Det godkjente GUI-sporet bruker et mørkt, kompakt arbeidsmiljø med turkis
aksent, tydelige statusfarger og høy informasjonstetthet. Løsningen er lagt som
et eget stilag over eksisterende `workbench.css`, slik at funksjoner,
tilgangskontroll, skjemaer og domeneregler fortsatt er felles.

## Innført

- mørkt tema som standard, med lyst tema fortsatt tilgjengelig;
- samlet toppfelt med globalt katalogsøk og hurtigtasten `Ctrl+K`;
- strammere navigasjon, typografi, paneler, tabeller, statuser og handlinger;
- Musikkarkiv med ISRC, radiostatus og eksplisitt filstatus i arbeidslisten;
- valgt innspilling med identitet, radiometadata, filreferanse og kilder i
  kontekstuelt sidepanel;
- responsiv visning som reduserer tabellkolonner på smale skjermer;
- samme visuelle statusprinsipper på rettighetsflatene.

Status vises alltid med tekst i tillegg til farge. «Registrert i Forvaltet
musikk» er fortsatt medlemskap, ikke en eierskapspåstand. Filstatus skiller
mellom registrert referanse, ikke kontrollert og kontrollert plassering.

Endringen har ingen modeller eller migrasjoner. Videre visuell utvikling bør
bygge på variablene og komponentene i
`workbench/static/workbench/sol-foundation.css` fremfor lokale sidestiler.
