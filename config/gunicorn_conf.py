from django.db import connections


def post_fork(server, worker):
    connections.close_all()

    # Warm up the deferred google.genai import in the background so the first
    # Gemini-backed request on this worker does not pay the ~13s schema build.
    from aivus_backend.core.llm import warm_up_gemini  # noqa: PLC0415

    warm_up_gemini()
