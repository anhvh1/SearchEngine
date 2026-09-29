import pytest
from search_engine.contracts import Profile
from search_engine.profiles import normalize


def make(mapping):
    return Profile(id='vendor', name='Vendor', version=1, match={'site_id':'lab'}, mapping=mapping)


def test_mapping_reads_object_array_and_structured_vendor_json():
    profile = make({'plate': {'path': 'payload.objects.0.value', 'required': True},
                    'name': {'path': 'payload.vendor', 'format': 'json', 'selector': 'person.name', 'required': True}})
    result = normalize(profile, {'payload': {'objects': [{'value': '30A12345'}], 'vendor': '{"person":{"name":"Reported name"}}'}})
    assert result == {'plate': '30A12345', 'name': 'Reported name'}


def test_mapping_xml_reads_leaf_and_rejects_entities():
    profile = make({'zone': {'path': 'payload.vendor', 'format': 'xml', 'selector': 'Zone/Name', 'required': True}})
    assert normalize(profile, {'payload': {'vendor': '<Event><Zone><Name>Gate</Name></Zone></Event>'}}) == {'zone':'Gate'}
    with pytest.raises(ValueError):
        normalize(profile, {'payload': {'vendor': '<!DOCTYPE r [<!ENTITY x SYSTEM "file:///secret">]><Event><Zone><Name>&x;</Name></Zone></Event>'}})
