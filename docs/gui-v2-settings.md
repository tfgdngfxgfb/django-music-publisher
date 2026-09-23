# Innstillinger i GUI v2

`/v2/innstillinger/` er tilgjengelig via tannhjulet i hovedmenyen.

Alle innloggede brukere kan velge lyst/mørkt tema, lydnivå og automatisk neste
spor. Valgene lagres i nettleseren; de synkroniseres ikke mellom enheter.
Tema og lydnivå bruker spillerens eksisterende nettleserinnstillinger.
Automatisk neste spor lagres separat per bruker i nettleseren.
Valgt master har fortsatt prioritet ved normal Recording-avspilling.

Administratorer kan dessuten:

- velge separate lagringsområder, startmapper og mappemaler for RAW og mastere;
- konfigurere eksisterende `RightsConfiguration.local_organization`;
- se driftsstatus og gå videre til bruker-/gruppeadministrasjon.

Felles endringer krever superbruker, aktivert `GUI_V2_WRITES_ENABLED` og POST
med CSRF. De loggføres i Django-administrasjonens `LogEntry`. Bytte av eksisterende
lokal organisasjon krever at konsekvensen bekreftes i skjemaet.

## Digitalisering

Migrasjonen `media_assets.0010_digitization_configuration` legger til én
konfigurasjonstabell. Den oppretter ingen innstillinger automatisk. Før første
lagring gjelder eksisterende serverstandarder:

- RAW: `raw_sources` hvis konfigurert, ellers `music_library`;
- master: `edited_masters` hvis konfigurert, ellers `music_library`;
- mappemaler fra `P7_RAW_FOLDER_TEMPLATE` og `P7_MASTER_FOLDER_TEMPLATE`.

Et lagret standardvalg gjelder umiddelbart for begge filvelgerne og «Finn mappe
for utgivelsen». Eksempel med Musikkarkiv som felles rot:

| Formål | Startmappe | Mappemal under startmappen |
| --- | --- | --- |
| RAW | Rå digitalisering | `{label}/{series}` |
| Master | Redigert master | `{catalogue_number} - {title}` |

Tom mappemal bruker startmappen direkte. Punktum (`.`) betyr lagringsroten.
Startmappen må finnes når innstillingene lagres. Mappemaler støtter bare
`{label}`, `{series}`, `{catalogue_number}` og `{title}`. Utrygge stier og
maluttrykk avvises. Søket går ikke rekursivt gjennom arkivet.

Disse valgene styrer hvor filvelgeren starter, ikke hvor eksisterende filer er
lagret. `FileLocation.storage_root_key` og relative filstier endres ikke.
Fysiske lagringsrøtter (`P7_MUSIC_ROOT`, `P7_RAW_SOURCE_ROOT`,
`P7_MASTER_SOURCE_ROOT`, `P7_GENERATED_MEDIA_ROOT`) og sikkerhetsbrytere for
filskriving styres fortsatt av serveroppsettet. Ingen `.env`-fil redigeres fra GUI.
