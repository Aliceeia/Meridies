import io
import struct
import wave
from connect.backend import voice
from connect.tools.repair_voice_containers import unwrap


def with_provenance():
    pcm=bytes(4800)
    base=voice.wav(pcm)
    metadata=b'C2PA provenance metadata, not audio.'
    chunk=b'LIST'+struct.pack('<I',len(metadata))+metadata
    if len(metadata)%2:chunk+=b'\0'
    return base[:4]+struct.pack('<I',len(base)+len(chunk)-8)+base[8:]+chunk


def test_existing_wav_is_not_wrapped_and_provenance_is_preserved():
    original=with_provenance()
    assert voice.wav(original)==original
    assert voice.seconds_of(original)==0.1
    with wave.open(io.BytesIO(voice.wav(original)),'rb') as audio:
        assert audio.readframes(audio.getnframes())==bytes(4800)


def test_raw_pcm_still_gets_correct_header():
    with wave.open(io.BytesIO(voice.wav(bytes(9600),24000)),'rb') as audio:
        assert audio.getframerate()==24000 and audio.getnframes()==4800


def test_old_double_wrapper_is_removed_without_cutting_speech_or_provenance():
    original=with_provenance()
    wrapped=b'RIFF'+struct.pack('<I',36+len(original))+b'WAVEfmt '+struct.pack('<IHHIIHH',16,1,1,24000,48000,2,16)+b'data'+struct.pack('<I',len(original))+original
    assert unwrap(wrapped)==original
    assert unwrap(original)==original
