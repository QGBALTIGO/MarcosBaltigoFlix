import asyncio
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

import httpx
import pytest

from app.config import get_settings
from app.result_service import ResultService, official_release_open
from app.tse import TSEClient
from tests.test_result_service import settings, DirectoryStub
from tests.test_parser import fixture


def runoff_settings():
    return replace(settings(), election_round=2, tse_election_code=6258, tse_state_election_code=6260)


def test_config_second_round_and_historical_offices(monkeypatch, tmp_path):
    for key in ['TSE_ELECTION_CODE', 'TSE_STATE_ELECTION_CODE', 'ELECTION_MODE']: monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('ELECTION_ROUND','2');monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'data.db'))
    conf=get_settings()
    assert conf.code_for_office('presidente')==6258
    assert conf.code_for_office('governador')==6260
    assert conf.code_for_office('senador')==6259
    assert conf.round_for_office('governador')==2
    assert conf.round_for_office('federal')==1
    monkeypatch.setenv('ELECTION_ROUND','3')
    with pytest.raises(RuntimeError): get_settings()


def test_second_round_release_gate():
    tz=ZoneInfo('America/Sao_Paulo')
    assert not official_release_open(2,datetime(2026,10,25,16,59,59,tzinfo=tz))
    assert official_release_open(2,datetime(2026,10,25,17,0,tzinfo=tz))
    assert not official_release_open(2,datetime(2026,10,4,18,0,tzinfo=tz))


@pytest.mark.parametrize('bad', ['election', 'round', 'environment'])
def test_wrong_election_round_or_simulation_never_enters_cache(bad):
    async def scenario():
        tse=TSEClient(runoff_settings());await tse.client.aclose()
        data=fixture();data.update(ele='6258',t='2',f='o')
        data[{'election':'ele','round':'t','environment':'f'}[bad]]={'election':'6257','round':'1','environment':'s'}[bad]
        tse.client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=data)))
        try:
            with pytest.raises(ValueError): await tse.fetch('br')
            assert tse.cached('br') is None
        finally: await tse.close()
    asyncio.run(scenario())


def test_second_round_urls_preserve_first_round_senate():
    async def scenario():
        tse=TSEClient(runoff_settings())
        try:
            assert '/6258/' in tse.result_url('br')
            assert '/6260/' in tse.result_url('ac',office='governador')
            assert '/6259/' in tse.result_url('ac',office='senador')
        finally: await tse.close()
    asyncio.run(scenario())


def test_pre_release_force_stays_zero_and_directory_coalesces_requests():
    async def scenario():
        tse=TSEClient(runoff_settings());await tse.client.aclose();calls=[]
        data=fixture();data.update(ele='6258',t='2',f='o')
        def response(request):calls.append(request.url);return httpx.Response(200,json=data)
        tse.client=httpx.AsyncClient(transport=httpx.MockTransport(response))
        service=ResultService(runoff_settings(),tse,DirectoryStub())
        try:
            with patch('app.result_service.official_release_open',return_value=False):
                results=await asyncio.gather(*(service.fetch('br',force=True) for _ in range(10)))
            assert len(calls)==1
            for result,changed in results:
                assert result.round==2 and result.election_code==6258 and result.total_votes==0 and result.valid_votes==0
                assert all(c.votes==0 and c.percentage==0 and not c.elected_flag for c in result.candidates)
                assert not changed
        finally: await tse.close()
    asyncio.run(scenario())


def test_governor_without_runoff_never_fetches_wrong_url():
    async def scenario():
        tse=TSEClient(runoff_settings());service=ResultService(runoff_settings(),tse,DirectoryStub())
        try:
            directory=await service.directory('governador','ms')
            assert directory['count']==0 and 'Não há segundo turno' in directory['note']
            with patch('app.result_service.official_release_open',return_value=True):
                with pytest.raises(ValueError,match='Não há segundo turno'): await service.fetch('ms',office='governador')
        finally: await tse.close()
    asyncio.run(scenario())


def test_published_second_round_is_used_after_release():
    async def scenario():
        tse=TSEClient(runoff_settings());await tse.client.aclose();data=fixture();data.update(ele='6258',t='2',f='o')
        tse.client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=data)))
        service=ResultService(runoff_settings(),tse,DirectoryStub())
        try:
            with patch('app.result_service.official_release_open',return_value=True):result,_=await service.fetch('br')
            assert result.round==2 and result.total_votes>0 and result.phase=='o'
        finally: await tse.close()
    asyncio.run(scenario())


def test_concurrent_visitors_share_one_upstream_call_even_with_refresh():
    async def scenario():
        conf=replace(runoff_settings(), result_cache_seconds=2)
        tse=TSEClient(conf);await tse.client.aclose();calls=[];data=fixture();data.update(ele='6258',t='2',f='o')
        def response(request):calls.append(request.url);return httpx.Response(200,json=data)
        tse.client=httpx.AsyncClient(transport=httpx.MockTransport(response))
        try:
            results=await asyncio.gather(*(tse.fetch('br',force=True) for _ in range(200)))
            assert len(calls)==1 and len(results)==200 and sum(changed for _,changed in results)==1
        finally:await tse.close()
    asyncio.run(scenario())


def test_unpublished_file_has_a_negative_cache():
    async def scenario():
        tse=TSEClient(replace(runoff_settings(),result_cache_seconds=2));await tse.client.aclose();calls=[]
        def response(request):calls.append(request.url);return httpx.Response(404)
        tse.client=httpx.AsyncClient(transport=httpx.MockTransport(response))
        try:
            for _ in range(10):
                with pytest.raises(httpx.HTTPStatusError):await tse.fetch('br',force=True)
            assert len(calls)==1
        finally:await tse.close()
    asyncio.run(scenario())
