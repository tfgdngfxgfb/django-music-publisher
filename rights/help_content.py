"""Authoritative Norwegian explanations for the master-rights domain."""

from dataclasses import dataclass


@dataclass(frozen=True)
class HelpTerm:
    slug: str
    title: str
    short: str
    long: str


def _term(slug, title, short, long):
    return HelpTerm(slug=slug, title=title, short=short, long=long)


RIGHTS_HELP = {
    "managed": _term(
        "forvaltet-musikk",
        "Forvaltet musikk",
        "Innspillingen inngår i organisasjonens forvaltede repertoar. Det betyr ikke i seg selv at organisasjonen eier masteren.",
        "Forvaltet musikk er en uttrykkelig repertoarregistrering. Den kan bygge på eierskap, administrasjon eller distribusjon, men registreringen er ikke i seg selv et rettighetsbevis.",
    ),
    "ownership": _term(
        "mastereierskap",
        "Mastereierskap",
        "Eierskap til det bestemte lydopptaket. Det er separat fra verk, administrasjon og distribusjon.",
        "Mastereierskap gjelder det bestemte lydopptaket. Eier, andel, territorium, periode, status og grunnlag må vurderes samlet. Filbesittelse, labelnavn og forvaltningsregistrering beviser ikke eierskap.",
    ),
    "administration": _term(
        "administrasjon",
        "Administrasjon",
        "Mandat til å forvalte masterrettigheten på vegne av rettighetshaveren. Det innebærer ikke automatisk eierskap eller distribusjon.",
        "Administrasjon kan omfatte rettighetsregistrering, metadataforvaltning, lisensoppfølging, vederlagsoppfølging og annen representasjon innenfor avtalt mandat. Det er separat fra eierskap og distribusjonsrett.",
    ),
    "distribution": _term(
        "distribusjon",
        "Distribusjon",
        "Mandat til å levere og administrere innspillingen hos distributører og plattformer. Det innebærer ikke automatisk eierskap.",
        "Distribusjon kan omfatte levering, distribusjonsmetadata, tilgjengeliggjøring og takedown innenfor avtalt mandat. Det innebærer verken mastereierskap eller generell administrasjon.",
    ),
    "rights_holder": _term(
        "rettighetshaver",
        "Rettighetshaver",
        "Parten som hevdes å eie, administrere eller distribuere innenfor dette kravet.",
        "Ved eierskap er rettighetshaver den påståtte eieren. Ved administrasjon og distribusjon er det parten som har det aktuelle mandatet.",
    ),
    "grantor": _term(
        "rettighetsgiver",
        "Rettighetsgiver",
        "Parten rettigheten eller mandatet kommer fra, når dette er kjent.",
        "Rettighetsgiver viser hvem som har overdratt eller gitt mandatet. Feltet kan være ukjent og dokumenterer ikke alene en sammenhengende rettskjede.",
    ),
    "share": _term(
        "andel",
        "Andel",
        "Registrert prosentandel fra 0 til 100. Ukjent andel skal stå ukjent.",
        "Andelen beskriver hvor stor del kravet gjelder. Registrerte andeler trenger ikke summere til 100 når katalogen er ufullstendig, og manglende andel må aldri gjettes.",
    ),
    "territory": _term(
        "territorieomfang",
        "Territorieomfang",
        "Hvor i verden rettigheten gjelder: hele verden, valgte land eller hele verden unntatt valgte land.",
        "Territorieomfang lagres med kontrollerte landkoder. En rettighet kan ha ulik innehaver eller status i forskjellige territorier.",
    ),
    "validity": _term(
        "gyldighetsperiode",
        "Gyldighetsperiode",
        "Registrert start- og sluttdato for rettigheten. Manglende sluttdato betyr bare at utløp ikke er registrert.",
        "Begge datoer kan være ukjente. En åpen sluttdato skal ikke tolkes som en automatisk juridisk evighet.",
    ),
    "unverified": _term(
        "uverifisert",
        "Uverifisert",
        "Kravet er registrert, men er ikke bekreftet av en autorisert bruker.",
        "Importerte og manuelt registrerte kildepåstander starter uverifisert. De må vurderes før de kan presenteres som bekreftet rett.",
    ),
    "confirmed": _term(
        "bekreftet",
        "Bekreftet",
        "En autorisert bruker har besluttet at kravet skal behandles som bekreftet på dagens grunnlag.",
        "Bekreftet er en loggført intern beslutning. Dokumentasjonsstyrken vises separat og kan fortsatt være lavere enn Dokumentert.",
    ),
    "disputed": _term(
        "bestridt",
        "Bestridt",
        "Kravet eller en relevant motstridende påstand er omstridt og hindrer en sikker konklusjon.",
        "Bestridelse bevarer kravet og kilden. Den skal brukes når reelle motstridende opplysninger må avklares, ikke som sletting.",
    ),
    "evidence_strength": _term(
        "dokumentasjonsstyrke",
        "Dokumentasjonsstyrke",
        "En menneskelig vurdering av hvor sterkt grunnlaget støtter kravet. Den er separat fra kravets status.",
        "Nivået går fra Ikke vurdert til Dokumentert. Dokumentert krever direkte dokumentasjon som dekker rettighetsforholdet; Sterkt underbygget kan brukes ved flere gode, uavhengige kilder uten komplett direkte rettskjede. Det beregnes ingen automatisk score.",
    ),
    "agreement": _term(
        "avtale",
        "Avtale",
        "Et mulig dokumentert grunnlag for rettigheten. En registrert avtale bekrefter ikke automatisk kravet.",
        "Avtalen beskriver parter, roller og periode og kan kobles til dokumentfiler. Hvert nødvendig ledd i en historisk rettskjede må fortsatt dokumenteres.",
    ),
    "source_record": _term(
        "kildepost",
        "Kildepost",
        "Den opprinnelige posten eller dokumentreferansen som opplysningen kommer fra.",
        "Kildeposten bevarer opprinnelig verdi og lokator. Orchard-, Mudi- og katalogdata er kildeopplysninger, ikke automatisk bekreftede rettigheter.",
    ),
}


RIGHTS_HELP_SECTIONS = tuple(RIGHTS_HELP.values())


RIGHTS_FORM_HELP = {
    "rights_holder": RIGHTS_HELP["rights_holder"],
    "grantor": RIGHTS_HELP["grantor"],
    "share": RIGHTS_HELP["share"],
    "territory_mode": RIGHTS_HELP["territory"],
    "territories": RIGHTS_HELP["territory"],
    "valid_from": RIGHTS_HELP["validity"],
    "valid_until": RIGHTS_HELP["validity"],
    "evidence_strength": RIGHTS_HELP["evidence_strength"],
    "source_record": RIGHTS_HELP["source_record"],
    "agreement": RIGHTS_HELP["agreement"],
}
