# Meridies / Meri

Routes: `/` minimal voice home; `/gallery` memory timeline; `/walk` 3D places.
`POST /api/meri/chat` takes JSON `question` and `viewer`. It returns text,
permission-filtered sources and optional server-built navigation or audio action.

Set `MONGODB_URI` and optionally `MONGODB_DATABASE` in `connect/.env` (Git ignored).
Install `connect/requirements.txt` into the existing Python environment, then run
`python connect/server.py`. Mongo is authoritative when configured; startup fails
if it cannot connect. Existing local records migrate once by id without overwriting
Mongo records. Local media/images remain caches for transcription and covers.
Keep the local media directory: images are not stored in Mongo by this change.

Meri retrieves only memories allowed by the existing sharing rules, plus rooms
in loaded CoStar scene plans. Retrieval currently uses lexical ranking, not a vector
index. Audio is retrieved by its transcript metadata; original recordings are
played through permission-checked audio endpoints. Grounded Gemini generation
uses only retrieved context; if it fails, the response reports retrieved records.
Identity is still the demo viewer switch, not authenticated login.

Microphone activation requires a browser gesture and permission. Say Hi Meri,
then ask a question; digital gallery navigates immediately. Text entry is available.
Gemini TTS quotas still apply, and the prepared environment speech library is
not fully generated yet. An unavailable voice leaves the response readable.
