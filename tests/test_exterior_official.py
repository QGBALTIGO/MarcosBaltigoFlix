import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.bu import parse_presidential_bu
from app.exterior import exterior_bulletin_loop, format_exterior_channel_message
from app.exterior_official import BASE, CACHE_KEY, INDEX, OfficialExteriorClient
from app.storage import Storage
from tests.test_second_round import runoff_settings

FIXTURES = Path(__file__).parent / 'fixtures/exterior'


def config():
    return replace(runoff_settings(), exterior_bu_enabled=True, exterior_bu_poll_seconds=30)


def wire_index():
    return {'f':'o', 'cdp':'3221', 'abr':[{'cd':'zz','mu':[
        {'cd':'29491','nm':'CAMBERRA','zon':[{'cd':'0001','sec':[{'ns':'0732'},{'ns':'9999','nsp':'0732'}]}]},
        {'cd':'29742','nm':'HAMAMATSU','zon':[{'cd':'0001','sec':[{'ns':'0960'}]}]},
    ]}]}


def mock_client(client, counts, index=None, overrides=None):
    def handler(request):
        url = str(request.url)
        counts.append(url)
        if overrides and url in overrides:
            return overrides[url]
        if url == INDEX:
            return httpx.Response(200, json=index or wire_index())
        name = request.url.path.rsplit('/',1)[-1]
        path = FIXTURES/name
        if path.exists():
            return httpx.Response(200, content=path.read_bytes())
        return httpx.Response(404)
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize('code,section,votes,blank,null,attendance', [
    ('29491','0732',{'13':85,'22':33},1,4,123),
    ('29742','0960',{'13':80,'22':330},9,11,430),
])
def test_real_official_second_round_bu_balances(code, section, votes, blank, null, attendance):
    raw = (FIXTURES/f'o03221zz{code}0001{section}-bu.dat').read_bytes()
    data = parse_presidential_bu(raw,code,'0001',section)
    assert data['votes']==votes and data['blank']==blank and data['null']==null
    assert data['attendance']==attendance
    assert sum(data['votes'].values())+data['blank']+data['null']==attendance
    sources = json.loads((FIXTURES/'sources.json').read_text())
    assert hashlib.sha256(raw).hexdigest()==next(s['sha256'] for s in sources['files'] if s['file']==f'o03221zz{code}0001{section}-bu.dat')


@pytest.mark.parametrize('before,after', [
    (b'20261025',b'20261004'),
    (b'\x82\x02\x0c\x95',b'\x82\x02\x0c\x94'),
    (b'\x0a\x01\x02',b'\x0a\x01\x01'),
    (b'\x02\x02\x18\x72',b'\x02\x02\x18\x71'),
    (b'\x82\x01\x55',b'\x82\x01\x56'),
    (b'\x02\x01\x0d\x02\x01\x0d',b'\x02\x01\x0c\x02\x01\x0c'),
])
def test_wrong_date_round_simulation_candidate_or_unbalanced_votes_are_rejected(before, after):
    raw = (FIXTURES/'o03221zz2949100010732-bu.dat').read_bytes()
    assert before in raw
    with pytest.raises(ValueError):
        parse_presidential_bu(raw.replace(before,after),'29491','0001','0732')


def test_wrong_requested_section_and_truncated_payload_are_rejected():
    raw = (FIXTURES/'o03221zz2949100010732-bu.dat').read_bytes()
    for data,code,section in [(raw,'29742','0732'),(raw,'29491','0733'),(raw[:-1],'29491','0732'),(raw+b'\0','29491','0732')]:
        with pytest.raises(ValueError):
            parse_presidential_bu(data,code,'0001',section)


def test_official_fetch_ignores_aggregated_sections_and_survives_restart(tmp_path):
    async def scenario():
        storage = Storage(str(tmp_path/'state.db'))
        await storage.init()
        counts = []
        client = OfficialExteriorClient(config(), storage)
        await client.client.aclose()
        mock_client(client,counts)
        try:
            with patch('app.exterior_official._closed',return_value=True):
                items = await client.fetch()
                assert len(items)==2 and client.health['last_error'] is None
                assert all(i.sections_counted==1 and i.sections_total==1 and i.official_bu for i in items)
                assert not any('9999' in url for url in counts)
                assert await storage.get_state(CACHE_KEY)
                first = {i.locality_code:i.fingerprint for i in items}
                assert len([u for u in counts if u.endswith('-bu.dat')])==2
        finally:
            await client.close()
        client = OfficialExteriorClient(config(),storage)
        await client.client.aclose()
        counts.clear()
        mock_client(client,counts)
        try:
            with patch('app.exterior_official._closed',return_value=True):
                items = await client.fetch()
            assert {i.locality_code:i.fingerprint for i in items}==first
            assert not any(u.endswith('-bu.dat') for u in counts)
            assert client.snapshot()['localities'][0]['bulletins'][0]['sha256']
            text = format_exterior_channel_message(items[0])
            assert '2º turno' in text and '1/1' in text and 'Fonte: TSE' in text
            assert 'não é a totalização nacional' in text
        finally:
            await client.close()
    asyncio.run(scenario())


def test_wrong_index_and_unpublished_sections_do_not_publish():
    async def scenario():
        for mode in ['wrong_index','missing']:
            client = OfficialExteriorClient(config())
            await client.client.aclose()
            index = wire_index()
            overrides = None
            if mode=='wrong_index':
                index['cdp']='3220'
            else:
                aux = BASE+'/dados/zz/29491/0001/0732/p003221-zz-m29491-z0001-s0732-aux.json'
                overrides={aux:httpx.Response(404)}
            counts=[]
            mock_client(client,counts,index,overrides)
            try:
                with patch('app.exterior_official._closed',return_value=True):
                    assert await client.fetch()==[]
                if mode=='missing':
                    assert len(counts)==2
                    assert client._probe_after['29491']>0
                else:
                    assert client.health['last_error']
            finally:
                await client.close()
    asyncio.run(scenario())


def test_rate_limit_sets_ten_minute_backoff():
    async def scenario():
        client = OfficialExteriorClient(config())
        await client.client.aclose()
        counts=[]
        mock_client(client,counts,overrides={INDEX:httpx.Response(429)})
        try:
            assert await client.fetch()==[]
            assert client._pause_until>0 and client.health['last_error']
            assert await client.fetch()==[]
            assert counts==[INDEX]
        finally:
            await client.close()
    asyncio.run(scenario())


def test_unmapped_unpublished_city_does_not_block_closed_localities():
    async def scenario():
        client=OfficialExteriorClient(config())
        await client.client.aclose()
        index=wire_index()
        index['abr'][0]['mu'][0]['nm']='Cidade sem fuso cadastrado'
        missing=BASE+'/dados/zz/29491/0001/0732/p003221-zz-m29491-z0001-s0732-aux.json'
        calls=[]
        mock_client(client,calls,index,overrides={missing:httpx.Response(404)})
        try:
            with patch('app.exterior_official._closed', side_effect=lambda name,now: None if name=='Cidade sem fuso cadastrado' else True):
                items=await client.fetch()
            assert [i.locality_code for i in items]==['29742']
            assert '29742' in calls[1] and calls[-1]==missing
        finally:
            await client.close()
    asyncio.run(scenario())


def test_publisher_deduplicates_restart_and_reaches_localities_after_batch_limit(tmp_path):
    async def scenario():
        storage=Storage(str(tmp_path/'state.db'))
        await storage.init()
        client=OfficialExteriorClient(config())
        await client.client.aclose()
        mock_client(client,[])
        with patch('app.exterior_official._closed',return_value=True):
            items=await client.fetch()
        await client.close()
        rows=[replace(items[0],country=f'Localidade {n}',locality_code=str(n)) for n in range(20)]
        sent=[]
        class Bot:
            exterior_last_message_id=123
            async def check_channel_ready(self): return True
            async def publish_exterior_update(self,text): sent.append(text); return True
        class Client:
            async def fetch(self): stop.set(); return rows
        with patch('app.exterior.asyncio.sleep'):
            for _ in range(3):
                stop=asyncio.Event()
                await exterior_bulletin_loop(config(),storage,Bot(),Client(),stop)
        assert len(sent)==20 and len(set(sent))==20
        last=json.loads(await storage.get_state('exterior:last_post'))
        assert last['round']==2 and last['official_bu'] and last['message_id']==123
    asyncio.run(scenario())
