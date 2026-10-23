"""Transcription des messages vocaux Telegram (fichiers .ogg) en texte."""

TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
DUREE_MAX_SECONDES = 5 * 60


async def transcribe(openai_client, audio: bytes, filename: str = "vocal.ogg") -> str:
    reponse = await openai_client.audio.transcriptions.create(
        model=TRANSCRIPTION_MODEL,
        file=(filename, audio),
        language="fr",
    )
    return reponse.text.strip()
