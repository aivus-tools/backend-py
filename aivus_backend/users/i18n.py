from django.conf import settings

DEFAULT_LANGUAGE = "en"


def supported_languages() -> set[str]:
    return {code for code, _ in settings.LANGUAGES}


def resolve_language(body_value: str | None, accept_language_header: str | None) -> str:
    supported = supported_languages()

    if body_value:
        code = body_value.strip().lower().split("-")[0]
        if code in supported:
            return code

    if accept_language_header:
        for part in accept_language_header.split(","):
            code = part.split(";")[0].strip().lower().split("-")[0]
            if code in supported:
                return code

    return DEFAULT_LANGUAGE


def user_language(user) -> str:
    user_settings = getattr(user, "settings", None)
    if user_settings and user_settings.language in supported_languages():
        return user_settings.language
    return DEFAULT_LANGUAGE
