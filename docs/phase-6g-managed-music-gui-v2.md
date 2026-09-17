# Fase 6G – Forvaltet musikk i GUI v2

## Avgrensning og forhåndsvurdering

6G bygger på 6F-baseline `9df15f2`. Inspeksjon av managed_music-modeller,
CreationForm, lifecycle/batchfunksjoner, rights.workflows, 6C scope/summaries,
Workbench, GUI-shell og eksisterende tester viste at 6D/6E allerede dekker
de nødvendige domenetransaksjonene. Ingen modell, migrasjon, ny status eller
endring av 6A–6E-domenesemantikk er nødvendig.

Forvaltning, mastereierskap, administrasjon og distribusjon forblir separate
begreper. ACTIVE er ikke bruksautorisasjon. Ingen oppdiktet kontraktskategori,
rettighetskonklusjon, management-timeline eller permanent oppfølgingsstatus
utledes fra illustrasjonene.

## Native sider og navigasjon

| Rute under `/v2/` | Navn | Formål |
| --- | --- | --- |
| `forvaltet-musikk/` | `managed_music` | Liste, filtre og valgt inspector |
| `forvaltet-musikk/registrer/` | `managed_onboard` | Eksplisitt registrering av P7-forvaltning |
| `forvaltet-musikk/<managed_id>/avklar/` | `managed_correct` | Administrativ avklaring av feil legacy-status |
| `forvaltet-musikk/<managed_id>/tilbakefor/` | `managed_return` | Eksplisitt avslutning av aldri-aktiv onboarding |

Hovedmenyen og arbeidsområdene på Hjem peker til den native listen.
Workbench-rutene beholdes som eldre alternativ. Recording/Rettigheter-lenker
bevarer return-kontekst med filtrering og valgt rad. Alle sidene bruker samme
GUI-v2-shell, temaer og footer-player. Playback-kode og F5-gjenoppretting endres
ikke. GET bruker ordinær intern GUI-navigasjon; POST følger dagens shell-regler.

## Liste, beregnet status og oppfølging

Kun faktiske ManagedRecording-medlemskap er med. Katalogvern via ManagedRelease
er ingen medlemskapserstatning; «Kun via forvaltet utgivelse» har ingen rad eller
filter her. Særtilfellet kan fortsatt håndteres i Musikkarkivet og onboardes
eksplisitt gjennom den samme tjenesten.

Liste og inspector bruker `management_states()` fra 6D. Effective status styrer
filtrene. Lagret status brukes bare som sekundær informasjon ved avvik.
`history_uncertain` vises som «Historikk må avklares», uten å opprette en fjerde
lifecycle-status. Manglende lokal organisasjon gir «Kan ikke vurderes» og ingen
tilbakeføringsadgang.

Standardutvalget viser ACTIVE/PENDING og historiske forhold med oppfølging.
Ordinær INACTIVE vises ved eksplisitt statusvalg eller «Alle».
Oppfølging bruker eksisterende state-signaler: usikker historikk, bestridt lokalt
grunnlag, PENDING uten plausibelt grunnlag og behov for reconciliation.
Ingen GET materialiserer status, og ingen «oppdater status nå»-handling tilbys.

Filtre: tittel/artist/ISRC, effective status, oppfølging, forvaltningskilde,
ownership-kategori, administrasjons- og distribusjonsgrunnlag.
Rettighetskolonner, -filtre og -lenker krever `rights.view_rightsclaim`.
Ownership bruker 6C `classify_ownership()`: ukjent andel er ikke null prosent,
og forskjellige territorielle andeler summeres ikke globalt.
Admin/distribution teller aktuelt bekreftet lokalt grunnlag via
`evaluate_management_basis()`, med separate uverifiserte/bestridte posisjoner.
Release-avgrenset bekreftet grunnlag merkes eksplisitt. Dette er ingen clearance.

Inspector viser identitet, effective state, eventuell lagret status, nåværende
grunnlag, bekreftet historikk, rettighetssammendrag, forvaltningskilde, reelle
timestamps og merknader. Den har native lenker til Recording og Rettigheter.
Cover og eventuell Spill-handling bruker eksisterende permission/resolver-logikk.

## Batch og paginering

`gui_v2/managed_music.py` er et read-only presentation-lag. SQL avgrenser søk og
kilde først. Kandidater leses i batcher på 200; relasjoner for identitet hentes
med select/prefetch. 6D henter claims/territorier/decision-historikk batchvis;
6C ownership/admin/distribution henter sitt ekstra claimgrunnlag batchvis.
Ingen per-rad kall til `build_recording_rights()` eller single-record resolver.

Effective filtre beregnes **før** paginering. En eksakt total krever vurdering
av alle SQL-avgrensede kandidater. Antall queries vokser per batch, ikke per
Recording. Beregningskostnaden vokser fortsatt med kandidater og claimhistorikk;
dette er et bevisst kompromiss uten persistent derived status eller ny SQL-motor.
Visningen beholder bare ønsket side og siste side (25 rader hver), ikke hele
resultatlisten. Ugyldig/høy sidenummer håndteres kontrollert.

Testen med 1 versus 50 medlemskap inkluderer claims, territorier og decisions
og krever identisk query-antall innen én batch. Videre optimalisering ved svært
store forvaltede kataloger bør bygge på målinger av reelle data, ikke ny
statussemantikk.

## Onboarding etter brukerpresisering

Normal GUI-v2-onboarding velger en **eksisterende MusicLibraryEntry/Recording**.
Tittel, artist, ISRC, varighet og tilgjengelige Releases vises read-only.
Det finnes ingen katalogmetadataform, «Opprett ny Recording» eller force-create
i denne brukerflyten. Nye innspillinger registreres først i Musikkarkivet.
Backend-støtte for nye Recordings, dublettkontroll og import/systembruk beholdes.
Poster uten MusicLibraryEntry avvises i GUI-formen, også ved direkte POST.
Ekstra innsendte katalogfelt brukes ikke til mutations.

Søket viser opptil 20 kandidater; `?recording=<uuid>` støttes. Formadapteren
gjenbruker eksisterende validering, med opptil én valgt Recording og kun dens
tilknyttede Releases som lovlige scopevalg.

Lagring går gjennom **`rights.workflows.onboard_managed_recording()`**.
Én rettighetstype velges. Mastereierskap krever kjent andel og tillater ikke
Release-scope. Administrasjon/distribusjon bruker ikke andel. Grantor,
territorier, åpne datogrenser, release_scope og dokumentasjonsfelt beholdes.
Forvaltningskilde, opprinnelig SourceRecord, Agreement og evidence strength er
separate. SourceRecord angis valgfritt med eksisterende UUID for å unngå å laste
hele arkivets provenance som en dropdown.

Resultatet forklares før lagring: PENDING-medlemskap og ett lokalt UNVERIFIED
claim. Verifikasjon skjer separat i 6F. Ingen auto-confirm, auto-ACTIVE eller
auto-onboarding uten uttrykkelig registrering.

## Legacy correction og tilbakeføring

Legacy-handlingen bruker **`rights.workflows.correct_legacy_management_history()`**.
Bare relevant `history_uncertain` tilbys normal handling. Lagret status og
usikkerhetsvurdering vises separat. Brukeren må begrunne og bekrefte korreksjonen.
Reell historisk forvaltning skal dokumenteres gjennom retrospektivt RightsClaim
og ordinær beslutning i 6F, ikke viskes ut med korreksjon.
Eksisterende workflow bevarer medlemskap/claims/decisions, oppretter permanent
SourceRecord-audit og setter trygg PENDING uten irrelevant status-only FLAC-sync.

Tilbakeføring bruker **`managed_music.services.return_to_music_library()`**.
Normal lenke krever `ManagementState.can_return_to_music_library`. POST
revaliderer under tjenestens eksisterende låser. Begrunnelse og bekreftelse er
påkrevd. Kun aldri faktisk aktiv PENDING uten current/history/plausible basis
eller usikker historikk kan tilbakeføres. Uverifisert, bestridt og framtidig
bekreftet grunnlag kan fortsatt blokkere etter legacy-korreksjon.

Tilbakeføring sletter bare ManagedRecording-medlemskapet. Recording,
MusicLibraryEntry, RightsClaims, RightsDecisions og provenance beholdes.
Dette er ikke «sett INACTIVE», og det finnes ingen ny «Ingen forvaltning»-status.
Faktisk historisk forvaltning beholdes som INACTIVE. Nytt senere P7-forhold
krever ny eksplisitt onboarding. Ingen automatisk tilbakeføring innføres.

## Permissions og visuell retning

Liste/action-sider krever managed_music.view_managedrecording og
catalogue.view_recording. Onboarding krever fortsatt superuser og add-management
+ add-claim. Legacy-korreksjon krever superuser, change-management og
decide-rightsclaim. Tilbakeføring krever delete-management. 6E/6D håndhever
permissions igjen i mutation-tjenestene. `GUI_V2_WRITES_ENABLED` håndheves ved
POST; skjuling/deaktivering i template er bare UX.

Illustrasjonene brukes for filter/liste/inspector, kompakte seksjoner og tydelig
konsekvensforklaring. Bevisste avvik: ingen oppdiktede bilder/data/timeline,
ingen bulk-checkboxes, ingen managed-release-only-filter, bare én initial
rettighetstype, ingen katalogoppretting i onboarding etter brukerpresisering,
ingen duplisert toppspiller og ingen fiktiv status etter tilbakeføring.
Eksisterende CSS-variabler brukes i lys/mørk modus. Smal layout stabler panelene,
tabellen får lokal scroll. Native lenker, labels, focus og tastatur beholdes.

## Verifikasjon og fasegrenser

25 nye målrettede GUI-tester dekker membership/default/derived filtre,
ownership/Release-scope, permissions, read-only GET/player-shell, onboarding
scope/rollback/katalogvern, legacy-audit og separat return, return blockers,
historikkbevaring, stale state og batchskalering. Full lokal SQLite-discovery,
Black 26.5.1, Django check, migration drift og diff check kjøres før commit.
Sluttkontroll: full lokal SQLite-discovery kjørte 591 tester, 12 hoppet over
(discovery rapporterte 599 før prosjektets testutvalg). Alle bestod.
Black 26.5.1 på de seks nye/endrede Python-filene, Django systemcheck,
`makemigrations --check --dry-run` og `git diff --check` var grønne.
Browserkontroll med isolerte minnedata bekreftet hovedliste/inspector,
onboarding-søk/read-only katalogidentitet, betingede scope-felt, tilbakeføringsside,
lys/mørk modus og felles player-shell. Ingen horisontal side-overflyt ble målt
ved 1600 eller smal 480 pikslers CSS-bredde. Lyd ble ikke demonstrert manuelt;
eksisterende automatiserte player-regresjoner inngikk i full kjøring.
PostgreSQL 17.11/full CI kjøres på GitHub etter push; lokal PostgreSQL kjøres ikke.

6H: Release-bulk og rights-matrix. 6I: global oppfølgingskø. Ingen endelig
ManagedRelease-workspace, Party/Work-endring, clearance, import, standardadapter,
intern scheduler eller generell metadata-writeback bygges i 6G.
Canonical domene forblir standardsnøytralt; framtidig RDR-N/CWR-mapping er utsatt.
