import base64
import io
import json
import urllib.error
from unittest.mock import patch
from connect.backend import voice


def quota(delay, daily=False):
    metric = 'generate_requests_per_model_per_day' if daily else 'generate_requests_per_minute'
    body = {'error': {'details': [
        {'@type':'google.rpc.QuotaFailure','violations':[{'quotaMetric':metric}]},
        {'@type':'google.rpc.RetryInfo','retryDelay':f'{delay}s'}]}}
    return urllib.error.HTTPError('https://example.invalid',429,'quota',{},io.BytesIO(json.dumps(body).encode()))


def test_daily_limit_on_one_model_does_not_block_another():
    audio={'candidates':[{'content':{'parts':[{'inlineData':{'mimeType':'audio/L16;rate=24000','data':base64.b64encode(bytes(4800)).decode()}}]}}]}
    with patch.object(voice,'MODELS',('one','two')), patch.object(voice.gemini,'api_key',return_value='test'), patch.object(voice.urllib.request,'urlopen',side_effect=[quota(80000,True),io.BytesIO(json.dumps(audio).encode())]):
        assert voice._synth_once('Hello','Sulafat',1).startswith(b'RIFF')


def test_short_limit_preserves_earliest_retry_and_is_not_daily():
    with patch.object(voice,'MODELS',('one','two')), patch.object(voice.gemini,'api_key',return_value='test'), patch.object(voice.urllib.request,'urlopen',side_effect=[quota(80000,True),quota(12)]):
        try:
            voice._synth_once('Hello','Sulafat',1)
        except voice.GeminiQuotaError as error:
            assert error.retry_after == 12
            assert not error.daily
        else:
            raise AssertionError('Expected structured quota failure')


def test_confirmed_daily_limits_are_reported_as_daily():
    with patch.object(voice,'MODELS',('one','two')), patch.object(voice.gemini,'api_key',return_value='test'), patch.object(voice.urllib.request,'urlopen',side_effect=[quota(80000,True),quota(79000,True)]):
        try:
            voice._synth_once('Hello','Sulafat',1)
        except voice.GeminiQuotaError as error:
            assert error.daily and error.retry_after == 79000
        else:
            raise AssertionError('Expected daily quota failure')
