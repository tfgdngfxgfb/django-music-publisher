# GUI v2 – trygt prototypeområde

- Branch: `feature/gui-v2-prototype`
- URL: `/v2/`
- Datakilde: statiske, tydelig fiktive Python-data i `gui_v2.views`.
- Innlogging: eksisterende Django-auth. Musikkarkiv og utgivelse krever de
  eksisterende visningstillatelsene.
- Database: prototypen importerer ingen domenemodeller eller tjenester og har
  bare GET-visninger. POST avvises. Ingen data kan lagres fra `/v2/`.
- Filer: prototypen importerer eller kaller ingen FLAC-, ingest-, sync- eller
  filskrivetjenester.

Templates, CSS og JavaScript ligger bare under `gui_v2` og kan erstattes eller
fjernes uten migrasjon. Dagens `/`, `/arbeid/`, admin og Workbench er uendret.
Neste designrunde kan erstatte de statiske skjermene og gradvis koble inn
eksisterende read-only tjenester etter at arbeidsflyten er godkjent.
