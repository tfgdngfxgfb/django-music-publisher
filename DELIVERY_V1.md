# Delivery v1

Delivery dokumenterer en kontrollert utlevering av konkrete filversjoner:

`Recording → RecordingMediaSelection.current_radio → DeliveryItem → nedlasting`

`DeliveryItem` fryser `FileAsset`, `FileLocation`, filnavn og et lite
katalogsnapshot. Senere bytte av gjeldende radiofil endrer derfor ikke gammel
leveransehistorikk. Preview er signert og bekreftelse avvises dersom Recording,
current radio, filplassering eller relevante revisjoner er endret.

Profilen **Intern – komplett** leverer en enkelt source-FLAC direkte og
byte-identisk. Bulk leveres som ZIP med CSV- og JSON-manifest. Profilen
**Ekstern radio** lager en kortlivet kopi med en eksplisitt allowlist av
standardtags og verifiserer at dekodet PCM er uendret. Source-filer åpnes kun
gjennom `media_assets.storage` og endres aldri.

Genererte kopier og ZIP-filer ligger under `P7_DELIVERY_ARTIFACT_ROOT`, utenfor
autoritative mediarøtter. Standard levetid er 24 timer og kan endres med
`P7_DELIVERY_ARTIFACT_TTL_HOURS`. Kommandoen
`cleanup_delivery_artifacts` fjerner utløpte artefakter, mens Delivery,
DeliveryItem, manifest-snapshot, artefaktmetadata og download-events beholdes.

Formål, mottaker og gjenbruksintensjon dokumenterer brukerens oppgitte hensikt.
De er ikke rights-clearance og oppretter ingen RightsClaim.

Delivery er heller ikke `ManagedRelease`, `ManagedRecording` eller fremtidig
digital distribusjon. Orchard, DDEX, eksterne portaler, offentlige lenker,
masterlevering og rights-readiness er uttrykkelig utsatt.
