from openai import OpenAI

from webapp.config import OPENAI_API_KEY


def get_openai_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY no esta definida. Ponla en .env para poder transcribir/analizar."
        )
    return OpenAI(api_key=OPENAI_API_KEY)
