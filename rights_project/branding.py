"""Application identity, separate from catalogue parties and publisher data."""

APP_NAME = "KRN DMA"
APP_ORGANIZATION = "Kristen Riksradio Norge"
APP_DESCRIPTION = "Digital Media Archive"
APP_FULL_NAME = f"{APP_ORGANIZATION}: {APP_DESCRIPTION}"


def application_identity(request):
    return {
        "app_identity": {
            "name": APP_NAME,
            "organization": APP_ORGANIZATION,
            "description": APP_DESCRIPTION,
            "full_name": APP_FULL_NAME,
        }
    }
