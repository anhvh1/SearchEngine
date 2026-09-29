import pytest
from fastapi import HTTPException
from search_engine.ai import grounded_answer


def test_model_cannot_return_citations_not_in_evidence(monkeypatch):
    import search_engine.ai as ai
    monkeypatch.setattr(ai, 'chat', lambda *a, **k: '{"answer":"Invented","keys":["forged"]}')
    item = {'key': 'real', 'source_guid': 'one', 'kind': 'alarm', 'site_id': 'lab',
            'source_id': 'cam', 'occurred_at': '2026-09-17T00:00:00Z', 'message': 'door'}
    with pytest.raises(HTTPException):
        grounded_answer('door', {'total': 1, 'items': [item]}, {'chat_model': 'test'})


def test_rag_receives_profile_context_and_normalized_attributes(monkeypatch):
    import search_engine.ai as ai
    observed = []
    def model(config, instructions, data, schema):
        observed.append(data)
        return '{"answer":"Door forced [real]","keys":["real"]}'
    monkeypatch.setattr(ai, 'chat', model)
    item = {'key': 'real', 'source_guid': 'one', 'kind': 'alarm', 'site_id': 'lab',
            'source_id': 'door', 'occurred_at': '2026-09-17T00:00:00Z', 'message': 'DOOR_FORCED',
            'attributes': {'zone': 'Warehouse'}, 'context': 'Door opened without valid authorization',
            'profile': {'id': 'acs', 'version': 2}, 'description': 'Reported by ACS'}
    result = grounded_answer('door', {'total': 1, 'items': [item]}, {'chat_model': 'test'})
    assert result['citations'][0]['key'] == 'real'
    assert observed[0]['evidence'][0]['attributes'] == {'zone': 'Warehouse'}
    assert observed[0]['evidence'][0]['profile'] == {'id': 'acs', 'version': 2}
    assert observed[0]['evidence'][0]['context'] == 'Door opened without valid authorization'


def test_chat_disables_thinking_and_bounds_output(monkeypatch):
    import search_engine.ai as ai
    observed = []
    monkeypatch.setattr(ai, 'ollama', lambda config, path, body: observed.append(body) or {'message': {'content': '{}'}})
    ai.chat({'chat_model': 'qwen3:4b'}, 'system', {'question':'x'}, {'type':'object'})
    assert observed[0]['think'] is False
    assert observed[0]['options']['num_predict'] == 512
