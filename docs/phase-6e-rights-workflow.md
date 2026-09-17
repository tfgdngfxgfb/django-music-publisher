# Fase 6E – kontrollert rettighetsarbeidsflyt

Normativ kontrakt, 17. september 2026. Bygger på 6A–6D, sist 6D `0fc32f2`.
Ingen nye modeller, migrasjoner eller persistente statuser. Dette er backend-
arbeidsflyt og minimale tilpasninger i eksisterende Workbench/admin/Release-form,
ikke 6F Rights GUI, use-clearance eller Delivery-clearance.

## Forhåndsvurdering

- RightsClaim hadde korrekt juridisk scope, immutable felt, ownership-validering
  og beskyttet Release-referanse. RightsDecision var append-only og hadde actor.
- Decision og superseding låste Recording først og kalte allerede 6D refresh.
  Tillatte overganger, terminal SUPERSEDED og same-status manglet eksplisitt regel.
  Eksisterende tester krevde ikke reaktivering av SUPERSEDED eller no-op-beslutning.
- Registrering og superuser-only onboarding var allerede adskilt i Workbench.
  Servicekallene manglet gjenbrukbar permission-policy; onboarding hardkodet WORLD.
- `superseded_by` er plural og kan uttrykke én-til-mange uten schemaendring.
  Den gamle tjenesten skrev én erstatning og én beslutning per kall.
- Avtalekobling brukte allerede kontrollert `_allow_claim_update` og DOCUMENTED.
  Evidence strength var beskyttet mot direkte redigering, men ingen normativ
  kontrakt krevde at ren dokumentasjonsstyrke måtte endre juridisk posisjon.
  Samme kontrollerte dokumentasjonsmekanisme kan derfor brukes for begge.
- Opprettelsesactor manglet, men en initial DOCUMENTED-hendelse er tilstrekkelig.
- Bulk hadde transaksjon og unik Recording-seleksjon, men ikke preview/stale-sjekk.
  Administratorstatus alene ble brukt som onboarding-samtykke. Dette er erstattet
  med eksplisitt valg og bekreftelse av forhåndsvisningen.
- Dokumentasjon låste claim først. GUI v2s sporgrid låste tracks før eventuelle
  Recording-endringer. Begge trengte justering for felles Recording-first orden.
- 6D identifiserte usikker legacy-historikk, men manglet eksplisitt avklaring.
  SourceRecord-audit som i 6A er tilstrekkelig; ingen ny historikkmodell trengs.
- Eksisterende note-kontrakt er frivillig ved rights-beslutning/superseding.
  Den beholdes. Legacy-korrigering og tilbakeføring krever begrunnelse.

## Tjenestelag og permissions

`rights/workflows.py` er inngangen for manuelle brukerhandlinger, også fremtidig
GUI v2. Lavnivåtjenestene i `rights/services.py` og
`managed_music/services.py` er fortsatt tilgjengelige for betrodde import-/system-
kall. De er ikke permission-grensen og må ikke kobles direkte til nye user views.

| Workflow | Permission / krav |
| --- | --- |
| `register_rights_claim` | `rights.add_rightsclaim`, eksisterende ManagedRecording |
| `onboard_managed_recording` | aktiv superuser, `managed_music.add_managedrecording`, `rights.add_rightsclaim` |
| `decide_rights_claim` | `rights.decide_rightsclaim` |
| `supersede_rights_claim[s]` | `rights.add_rightsclaim` + `rights.decide_rightsclaim` |
| `document_rights_claim` | `rights.change_rightsclaim`; avtaleendring krever også `rights.manage_agreement` |
| Release preview/apply | `catalogue.view_release` + `rights.add_rightsclaim`; onboarding krever ovenstående superuser-policy |
| `correct_legacy_management_history` | aktiv superuser, `managed_music.change_managedrecording` + `rights.decide_rightsclaim`, begrunnelse |
| eksisterende `return_to_music_library` | `managed_music.delete_managedrecording`, begrunnelse og 6D eligibility |

Eksisterende staff/view-permissions og GUI-v2-writeflag beholdes i views.
Agreement-/Party-/Document-administrasjon beholder etablerte permissions.
Eksisterende claims kan dokumenteres eller korrigeres historisk selv om
medlemskapet senere er tilbakeført; ny normal registrering krever medlemskap.
Workbench beholder sin strengere managed-grense for decision-skjemaet.
Ingen delete-handling åpnes for claims eller beslutninger.

## Registrering og onboarding

Normal registrering av nye krav krever eksisterende ManagedRecording. Relevante
tredjepartskrav er tillatt der. Ordinære, aldri forvaltede Recordings får ikke en
generell Rights-workflow. Importgrensesnittet er separat og bygges ikke ut her.

Onboarding er atomisk: eventuell ny Recording, eksisterende/ny MusicLibraryEntry,
ManagedRecording PENDING, lokalt UNVERIFIED claim, initial DOCUMENTED med actor.
Feil ruller tilbake alt. Eksisterende dublett-/ISRC-kontroller beholdes, også når
«Opprett ny likevel» brukes. Onboarding av allerede forvaltet Recording avvises.

Første claim kan uttrykke type, grantor, ownership-andel, WORLD/INCLUDE/EXCLUDE,
territorier, datoer, release_scope, evidence strength, SourceRecord, Agreement og
merknad. Holder er alltid konfigurert lokal organisasjon. Ownership krever
oppgitt andel i onboarding; administration/distribution tillater ikke andel.
Ownership kan ikke være Release-scoped. Release-scoped onboarding krever en
ikke-tom territoriell avgrensning etter 6C, som all annen onboarding, og en
eksisterende Recording som faktisk forekommer på den angitte Release-en.
ManagedRelease-only opphører først når ManagedRecording faktisk opprettes.
Registrering bekrefter ikke rettigheter eller materialiserer ACTIVE.

## Beslutningsmatrise

| Fra | Tillatte beslutninger |
| --- | --- |
| UNVERIFIED | CONFIRMED, DISPUTED, REJECTED |
| CONFIRMED | DISPUTED, REJECTED |
| DISPUTED | CONFIRMED, REJECTED |
| REJECTED | CONFIRMED, DISPUTED |
| SUPERSEDED | ingen |

Same-status avvises. Bruk DOCUMENTED for merknader uten statusendring. REJECTED
kan revurderes når samme juridiske posisjon fortsatt vurderes; korrigert innhold
skal supersedes. SUPERSEDED er terminal, også gjennom lavnivå decision-service.
Decision lagres med actor, claim-status endres kontrollert, så kjører 6D refresh.
Ownership-conflict valideres med 6C under Recording-lås før bekreftelse. Feil
etterlater verken delvis audit eller status. Notes er fortsatt frivillig.

## Superseding

`supersede_rights_claims(previous, replacements=[...], user=..., note=...)`
erstatter én posisjon med én eller flere. Alle inputrader valideres før opprettelse.
Hver replacement er UNVERIFIED, har samme Recording/right_type og peker til
previous. Holder, grantor, share, territorium, periode og lovlig release_scope
kan korrigeres. Utelatt release_scope bevarer tidligere scope; eksplisitt None
betyr generell Recording-kontekst. Øvrige replacement-verdier angis av caller.

Previous blir SUPERSEDED én gang. Én superseding-beslutning på previous lister
samtlige nye UUID-er og begrunnelse. Nye claims får DOCUMENTED med actor og
referanse til previous. 6D refresh kjører én gang etter hele settet. Alle feil
ruller tilbake hele transaksjonen. En allerede superseded previous kan ikke
erstattes på nytt; oppdeling må sendes samlet. Single-helper er bevart.

Et claim er fortsatt én selvstendig juridisk posisjon. Flere evidenskilder for
samme posisjon er dokumentasjon, ikke additive duplikatclaims.

## Dokumentasjon og audit

`document_rights_claim(claim, user=..., agreement=..., evidence_strength=...,
note=...)` tillater bare disse dokumentasjonsfeltene. Utelatt verdi bevares;
agreement=None fjerner koblingen med audit. Endringer logges med gamle/nye
UUID-er eller verdier og actor i DOCUMENTED. En ren merknad er tillatt, men et
helt tomt no-op-kall avvises. Også REJECTED/SUPERSEDED kan dokumenteres.

SourceRecord på originalclaimet, juridisk scope og VerificationStatus endres
ikke. Avtalens datoer eller status kopieres aldri automatisk til claimet.
Dokumentasjonsstyrke er fortsatt uavhengig av bekreftet status og applicability.
6Ds bekreftelseshistorikk ignorerer DOCUMENTED. Ingen falsk CONFIRMED-hendelse
opprettes for å logge registrering eller ny dokumentasjon.

## Release-bulk: preview og apply

Preview er read-only og returnerer `ReleaseRegistrationPlan`: unike Recordings,
eksisterende medlemskap/status, onboarding-antall, juridisk utgivelsesscope,
blockers og en signert token. Seleksjonen er avgrenset til 1–500 Recordings.
Skjemaet beholder de foreslåtte rettighetsfeltene ved preview.

Tokenen gjelder i 30 minutter, er bundet til actor, Release, valgte input,
spor-/Recording-/medlemskaps-/claim-revisjoner, claim-territorier, refererte
objekter og lokal organisasjonskonfigurasjon. Den inneholder bare et hash av
grunnlaget. Ingen persistent preview-modell eller sessionsnapshot opprettes.

Apply låser valgte Recordings i UUID-rekkefølge, deretter relevante tracks,
beregner planen igjen og sammenligner signert grunnlag. Endret seleksjon, scope,
claim, track eller medlemskap krever ny preview. Permissions kontrolleres på
nytt. Planen kan ikke gjenbrukes etter vellykket apply fordi claims/membership
har endret seg. Ingen feilrad eller halv onboarding blir lagret.

General er eksplisitt standard. Bare valgt «denne utgivelsen» setter juridisk
release_scope, og da bare for administration/distribution. Onboarding-checkbox
må velges eksplisitt, og krever administrator og lokalt kvalifiserende claim.
Tredjepartsregistrering på en ikke-forvaltet Recording blokkeres. Valg av
utgivelse eller ManagedRelease innebærer aldri eierskap eller onboarding.

Bulk har en konservativ planregel: et foreslått ownership-claim som sammen med
eksisterende bekreftede posisjoner overstiger 100 % i faktisk overlapp, blokkerer
bulk. Dette bruker samme 6C-konfliktberegning; claimet ville fortsatt blitt lagret
UNVERIFIED. En individuell uavklart/motstridende posisjon kan registreres gjennom
vanlig claim-workflow og vurderes separat. Dette er ikke use-clearance.

## Usikker legacy-historikk

Faktisk historisk forvaltning dokumenteres med reelt retrospektivt claim og
vanlig bekreftelse. Ingen tjeneste oppfinner WORLD/100 %-grunnlag eller historisk
bekreftelse for å få lifecycle til å passe.

Hvis legacy-statusen faktisk var feil, kan en autorisert administrator eksplisitt
kalle `correct_legacy_management_history`. Den låser Recording og ManagedRecording,
beregner 6D state på nytt og krever history_uncertain uten current eller confirmed
history. SourceRecord lagrer actor, begrunnelse, objekt-ID-er, gammel status/revisjon,
ny PENDING og om plausible claims gjenstår. Kildedata er immutable etter dagens
SourceRecord-regler. Medlemskap, claims og decisions slettes eller omskrives ikke.

Bare denne eksplisitte korrigeringen materialiserer PENDING som historikkavklaring.
Den bruker eksisterende suppression for status-only FLAC-sync. Deretter er vanlig
6D state autoritativ: UNVERIFIED, DISPUTED og future CONFIRMED kan fortsatt blokkere
tilbakeføring. Tilbakeføring er en annen, eksplisitt og auditert 6A/6D-handling.
Legacy-korrigering har backend-service; endelig betjeningsflate kommer senere.

## Låsing og regresjonsgrenser

Alle manuelle claim-mutasjoner og legacy-korrigering bruker Recording-first
orden som 6D. Dokumentasjon leser claimet på nytt etter lås. Bulk låser flere
Recordings sortert, så tracks. GUI-v2-sporgrid låser nå gamle/valgte Recordings
før tracks; endret Recording-seleksjon mens lås avventes avvises.

Låsetester kjører bare der databasen støtter `select_for_update` (PostgreSQL CI).
De dekker samtidig onboarding/superseding, dokumentasjon versus decision,
claimregistrering versus tilbakeføring, legacy-korrigering versus bekreftelse,
og stale bulk etter venting på Recording-lås.

6A-historikk/tilbakeføring, 6B objektvis autoritet og radiometadata, 6C scope og
6D lifecycle gjenbrukes. Ingen vanlig GET reconciler lifecycle. Ingen workflow
kaller automatisk tilbakeføring. ManagedRelease-status endres ikke. Ingen ny
filskrivingsmotor, scheduler eller endring av FLAC/OneTagger-autoritet.

## Videre avklaringer for 6F–6I

- Hvordan presentere og redigere flere replacement-posisjoner samtidig i GUI?
- Hvor skal autorisert legacy-korrigering og separat tilbakeføring ligge?
- Hvilke beslutninger bør kreve begrunnelse når den endelige UX-kontrakten låses?
- Oppfølgingskø, Rights-dashboard og Release-matrise bygges senere over disse
  tjenestene; de skal ikke innføre egne scope-/lifecycle-/permission-regler.

## Varig arkitekturkrav: fremtidig standardinteroperabilitet

Dette utvider ikke 6E. Etter fase 6 planlegges et eget standard-/mappinggrunnlag.
Etter Party/ArtistIdentity-fasen skal Recording-/master-/rights-domenet kunne
mappes operativt til DDEX RDR-N. Work/publishing-fasen har CWR-kompatibilitet som
eksplisitt designkrav. Senere standarder, eksempelvis RDR-C, RDR-R, RDR-RCC og
CAF/CRD, skal kunne legges til gjennom versjonerte adaptere uten redesign av
kjernedomenet. Dette er en arkitekturretning, ikke dokumentert standarddekning.

P7s kanoniske Recording, Release, Party, RightsClaim, Agreement og senere Work
forblir standardsnøytrale. Standardversjoner, mapping og transport skal ligge
utenpå domenet. Identiteter, holder/grantor, ownership/administration/distribution,
andeler, territorium, periode, Release-scope, opprinnelig provenance, senere
dokumentasjon, Agreement og historikk skal bevares som adskilte konsepter.
6E-workflowen skal verken slå dem sammen eller overskrive originalkilden.

Manglende opplysninger, for eksempel InitialProducer, contributor identity eller
publisher-data, skal senere kunne rapporteres som manglende/ikke-kartlagt. De
skal ikke konstrueres ut fra andre P7-data. Party-/artist-identitet skal ikke
improviseres i rettighetsarbeidsflyten.

6E innfører ingen RDR-N XML, CWR, import/eksport, standardspesifikke validators,
message storage, DDEX-transport eller schemafelt for interoperabilitet. Full
gap-analyse hører til senere standard-/mappingarbeid (6K). Dersom en konkret
workflow-/modellbeslutning hindrer senere tapsfri eller meningsfull mapping,
skal den beslutningen stoppes og problemet rapporteres før gjennomføring;
standarden skal ikke implementeres som en omvei i 6E.
