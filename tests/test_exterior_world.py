import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
from telegram.error import BadRequest, NetworkError, RetryAfter

from app.bot import ElectionBot
from app.exterior import exterior_bulletin_loop, format_exterior_channel_message
from app.exterior_official import CATALOG, INDEX, SCAN_KEY, OfficialExteriorClient, ScanBudget
from app.storage import Storage
from tests.test_exterior_official import FIXTURES, config, mock_client

WORLD=json.loads((FIXTURES/'world-index.json').read_text(encoding='utf-8'))


def test_every_official_exterior_locality_has_country_and_valid_timezone():
    municipalities={p['cd']:p['nm'] for state in WORLD['abr'] if state['cd']=='zz' for p in state['mu']}
    assert len(municipalities)==186 and set(CATALOG)==set(municipalities)
    assert all(CATALOG[code]['name']==name for code,name in municipalities.items())
    for place in CATALOG.values():
        assert place['country']
        ZoneInfo(place['timezone'])
    assert {code for code,p in CATALOG.items() if p['country']=='Japão'}=={'29742','30198','30627'}


def test_world_scan_reaches_all_localities_despite_missing_files_and_restarts(tmp_path):
    async def scenario():
        storage=Storage(str(tmp_path/'world.db'));await storage.init()
        seen=[]
        async def section(place,zone,number):
            seen.append(place['code'])
            request=httpx.Request('GET','https://source.test/expected-but-unpublished')
            raise httpx.HTTPStatusError('not yet available',request=request,response=httpx.Response(404,request=request))
        for _ in range(17):
            client=OfficialExteriorClient(config(),storage)
            await client.client.aclose()
            mock_client(client,[],WORLD)
            client._section=section
            try:
                with patch('app.exterior_official._closed',return_value=True):
                    await client.fetch()
                assert client.health['last_error'] is None
                assert client.health['localities_total']==186
            finally:await client.close()
        state=json.loads(await storage.get_state(SCAN_KEY))
        assert set(state['consulted'])==set(CATALOG)==set(seen)
        assert len(seen)==186  # Each locality was reached once, without retrying the first one forever.
    asyncio.run(scenario())


def test_unknown_timezone_is_not_an_allowlist_and_budget_resumes_next_city(tmp_path):
    async def scenario():
        storage=Storage(str(tmp_path/'state.db'));await storage.init()
        synthetic={'f':'o','cdp':'3221','abr':[{'cd':'zz','mu':[
            {'cd':str(80000+n),'nm':f'Localidade inédita {n}','zon':[{'cd':'0001','sec':[{'ns':'0001'}]}]} for n in range(20)]}]}
        seen=[]
        for _ in range(6):
            client=OfficialExteriorClient(config(),storage)
            await client.client.aclose();mock_client(client,[],synthetic)
            calls=[]
            async def section(place,zone,number):
                if len(calls)==4:raise ScanBudget()
                calls.append(place['code']);seen.append(place['code']);return False
            client._section=section
            try:
                await client.fetch()
                assert client.health['localities_total']==20
                assert len(client.health['unmapped_localities'])==20
            finally:await client.close()
        assert set(seen)=={str(80000+n) for n in range(20)}
    asyncio.run(scenario())


def test_country_aggregation_has_full_denominator_and_no_duplicate_sections():
    client=OfficialExteriorClient(config())
    client._places=[{'code':code,'name':CATALOG[code]['name'],'country':'Japão','sections':[('0001','0001')]} for code in ['29742','30198','30627']]
    common={'votes':{'13':10,'22':30},'blank':1,'null':2,'source_url':'https://source.test/aux','bu_url':'https://source.test/bu','sha256':'fixture'}
    client._records={'29742:0001:0001':dict(common),'30198:0001:0001':dict(common)}
    result=client._aggregate()
    assert len(result)==1 and result[0].country=='Japão'
    assert result[0].sections_counted==2 and result[0].sections_total==3
    assert result[0].localities_counted==2 and result[0].localities_total==3
    assert {c.name:c.votes for c in result[0].candidates}=={'Lula':20,'Flávio Bolsonaro':60}
    assert 'Localidades com BUs: <b>2/3</b>' in format_exterior_channel_message(result[0])
    client._records['29742:0001:0001']['votes']={'13':0,'22':0}
    client._records.pop('30198:0001:0001')
    zero=client._aggregate()[0]
    assert zero.sections_counted==1 and all(c.votes==0 and c.percentage is None for c in zero.candidates)
    asyncio.run(client.close())


def test_country_message_is_updated_and_not_duplicated_after_restart(tmp_path):
    async def scenario():
        storage=Storage(str(tmp_path/'state.db'));await storage.init()
        client=OfficialExteriorClient(config())
        await client.client.aclose();mock_client(client,[])
        with patch('app.exterior_official._closed',return_value=True):rows=await client.fetch()
        await client.close()
        item=rows[0];sent=[]
        class Bot:
            exterior_last_message_id=901
            async def check_channel_ready(self):return True
            async def publish_exterior_update(self,text,message_id=None):
                sent.append(message_id);return True
        class Client:
            async def fetch(self):stop.set();return [current]
        for current in [item,item,replace(item,sections_total=2,sections_counted=2),replace(item,sections_total=2,sections_counted=2)]:
            stop=asyncio.Event()
            with patch('app.exterior.datetime') as clock,patch('app.exterior.asyncio.sleep'):
                clock.now.return_value=datetime(2026,10,25,12,tzinfo=timezone.utc)
                await exterior_bulletin_loop(config(),storage,Bot(),Client(),stop)
        assert sent==[None,901]
        key='exterior:country:r2:'+hashlib.sha1(item.country.casefold().encode()).hexdigest()
        saved=json.loads(await storage.get_state(key))
        assert saved['message_id']==901 and saved['fingerprint']!=item.fingerprint
    asyncio.run(scenario())


def test_telegram_edits_country_and_recreates_only_deleted_messages(tmp_path):
    async def scenario():
        bot=ElectionBot(config(),None,Storage(str(tmp_path/'db')),None)
        telegram=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)),edit_message_text=AsyncMock())
        bot.application=SimpleNamespace(bot=telegram);bot.channel_chat_id=-100123
        bot.check_channel_ready=AsyncMock(return_value=True)
        assert await bot.publish_exterior_update('primeiro')
        assert await bot.publish_exterior_update('atualizado',message_id=42)
        assert telegram.send_message.await_count==1 and telegram.edit_message_text.await_count==1
        telegram.edit_message_text.side_effect=NetworkError('network fixture')
        assert not await bot.publish_exterior_update('retry later',message_id=42)
        assert telegram.send_message.await_count==1
        telegram.edit_message_text.side_effect=BadRequest('Message to edit not found')
        assert await bot.publish_exterior_update('recriado',message_id=42)
        assert telegram.send_message.await_count==2
    asyncio.run(scenario())


def test_telegram_retry_after_defers_pending_country_without_extra_requests(tmp_path):
    async def scenario():
        bot=ElectionBot(config(),None,Storage(str(tmp_path/'db')),None)
        telegram=SimpleNamespace(send_message=AsyncMock(side_effect=RetryAfter(15)))
        bot.application=SimpleNamespace(bot=telegram);bot.channel_chat_id=-100123
        bot.check_channel_ready=AsyncMock(return_value=True)
        assert not await bot.publish_exterior_update('país pendente')
        assert not await bot.publish_exterior_update('aguardar pausa')
        assert telegram.send_message.await_count==1
        assert bot.exterior_retry_at>0
    asyncio.run(scenario())
