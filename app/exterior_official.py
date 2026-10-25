"""All exterior localities, using official second-round BUs and a durable fair queue."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .bu import parse_presidential_bu
from .config import Settings
from .exterior import ExteriorCandidate, ExteriorCountryResult
from .storage import Storage

log = logging.getLogger(__name__)
BASE = 'https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3221'
INDEX = BASE + '/config/zz/zz-p003221-cs.json'
CACHE_KEY = 'exterior:official:3221:sections:v1'
SCAN_KEY = 'exterior:official:3221:scan:v2'
RECEIPT_DATES = {'25/10/2026', '26/10/2026'}
CATALOG = json.loads((Path(__file__).parent/'data/exterior_locations_2026.json').read_text(encoding='utf-8'))['localities']


def _normal(value: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', value) if c.isascii() and c.isalnum()).lower()


ZONE_HINTS = {_normal(v['name']): v['timezone'] for v in CATALOG.values()}


def _closed(name: str, now: datetime) -> bool | None:
    zone = ZONE_HINTS.get(_normal(name))
    if not zone:
        return None  # Unknown names are still scanned, without a geographic allowlist.
    local = now.astimezone(ZoneInfo(zone))
    return (local.year, local.month, local.day, local.hour) >= (2026, 10, 25, 17)


class ScanBudget(Exception):
    """Resume from the durable cursor on the next pass."""


class OfficialExteriorClient:
    def __init__(self, settings: Settings, storage: Storage | None = None):
        self.settings, self.storage = settings, storage
        self.client = httpx.AsyncClient(timeout=12, follow_redirects=False,
            limits=httpx.Limits(max_connections=4), headers={'User-Agent':'ResultadoEleicoesBot/2.0'})
        self._records: dict[str, dict] = {}
        self._places: list[dict] = []
        self._loaded = False
        self._index_checked = 0.0
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0
        self._pause_until = 0.0
        self._probe_after: dict[str, float] = {}
        self._next_section: dict[str, int] = {}
        self._consulted: set[str] = set()
        self._cursor = 0
        self._requests_left: int | None = None
        self._deadline = 0.0
        self._items: list[ExteriorCountryResult] = []
        self._locality_items: list[ExteriorCountryResult] = []
        self.health = {'running':False, 'source':'TSE · BU oficial · 2º turno',
            'last_check_at':None, 'last_error':None, 'localities':0, 'sections':0,
            'localities_total':0, 'countries_total':0, 'countries_with_bu':0,
            'localities_consulted':0, 'countries_consulted':0, 'unmapped_localities':[],
            'scan_cursor':0, 'request_limit_per_pass':120, 'missing_limit_per_pass':12}

    async def _get(self, url: str) -> httpx.Response:
        async with self._rate_lock:
            if time.time() < self._pause_until:
                raise RuntimeError('TSE polling is in backoff')
            if self._requests_left is not None:
                if self._requests_left <= 0 or time.monotonic() >= self._deadline:
                    raise ScanBudget()
                self._requests_left -= 1
            await asyncio.sleep(max(0, self._last_request + .25 - time.monotonic()))
            self._last_request = time.monotonic()
        response = await self.client.get(url)
        if response.status_code in {403, 429}:
            self._pause_until = time.time() + 600
        response.raise_for_status()
        return response

    async def _load(self) -> None:
        if self._loaded:
            return
        if self.storage:
            raw = await self.storage.get_state(CACHE_KEY)
            if raw:
                payload = json.loads(raw)
                if payload.get('pleito') != 3221:
                    raise ValueError('Wrong persisted exterior election')
                self._records = payload.get('records', {})
            scan = await self.storage.get_state(SCAN_KEY)
            if scan:
                state = json.loads(scan)
                self._cursor = int(state.get('cursor',0))
                self._next_section = {k:int(v) for k,v in state.get('next_section',{}).items()}
                self._probe_after = {k:float(v) for k,v in state.get('probe_after',{}).items()}
                self._consulted = set(state.get('consulted',[]))
        self._loaded = True

    async def _index(self) -> None:
        if self._places and time.time() - self._index_checked < 600:
            return
        data = (await self._get(INDEX)).json()
        if str(data.get('f','')).lower() != 'o' or str(data.get('cdp')) != '3221':
            raise ValueError('TSE index is not the official second round')
        places = []
        for state in data['abr']:
            if state['cd'].lower() != 'zz':
                continue
            for place in state['mu']:
                code = str(place['cd']).zfill(5)
                if not re.fullmatch(r'\d{5}', code):
                    raise ValueError('Invalid TSE municipality')
                sections = []
                for zone in place['zon']:
                    for section in zone['sec']:
                        if section.get('nsp'):
                            continue
                        z,s = str(zone['cd']).zfill(4),str(section['ns']).zfill(4)
                        if not re.fullmatch(r'\d{4}',z) or not re.fullmatch(r'\d{4}',s):
                            raise ValueError('Invalid TSE section')
                        sections.append((z,s))
                meta = CATALOG.get(code,{})
                places.append({'code':code, 'name':place['nm'], 'sections':sorted(set(sections)),
                    'country':meta.get('country',place['nm'].title()), 'timezone':meta.get('timezone')})
        if not places:
            raise ValueError('TSE index has no exterior localities')
        # No priority country list: the durable cursor visits the whole index.
        self._places = sorted(places,key=lambda p:p['code'])
        self._index_checked = time.time()
        self.health.update(localities_total=len(places), countries_total=len({p['country'] for p in places}),
            unmapped_localities=[p['code'] for p in places if p['code'] not in CATALOG])

    async def _section(self, place: dict, zone: str, section: str) -> bool:
        code = place['code']; key = f'{code}:{zone}:{section}'
        folder = f'{BASE}/dados/zz/{code}/{zone}/{section}'
        aux_url = f'{folder}/p003221-zz-m{code}-z{zone}-s{section}-aux.json'
        aux = (await self._get(aux_url)).json()
        if str(aux.get('f','')).lower() != 'o' or aux.get('dg') not in RECEIPT_DATES:
            raise ValueError('Wrong exterior auxiliary phase or date')
        if _normal(aux.get('st','')) not in {'recebida','totalizada'}:
            return bool(self._records.pop(key,None))
        hashes = sorted(aux.get('hashes',[]),key=lambda h:(h.get('dr',''),h.get('hr','')),reverse=True)
        selected = next((h for h in hashes if _normal(h.get('st','')) in {'recebido','totalizado'}),None)
        if not selected or selected.get('dr') not in RECEIPT_DATES:
            raise ValueError('No accepted second-round BU hash')
        hash_ = selected['hash']
        if not re.fullmatch('[a-fA-F0-9]{16,256}',hash_):
            raise ValueError('Invalid TSE path hash')
        if self._records.get(key,{}).get('hash') == hash_:
            return False
        file = next((f['nm'] for f in selected.get('arq',[]) if f.get('tp')=='bu'),None)
        if not file or not re.fullmatch(rf'o03221zz{code}{zone}{section}-bu(?:sa)?\.dat',file):
            raise ValueError('Missing or invalid BU filename')
        url = f'{folder}/{hash_}/{file}'
        raw = (await self._get(url)).content
        record = parse_presidential_bu(raw,code,zone,section)
        record.update(hash=hash_,source_url=aux_url,bu_url=url,sha256=hashlib.sha256(raw).hexdigest())
        self._records[key] = record
        return True

    def _result(self, label: str, codes: tuple[str,...], country_group: bool) -> ExteriorCountryResult | None:
        places = [p for p in self._places if p['code'] in codes]
        records = [self._records[f"{p['code']}:{z}:{s}"] for p in places for z,s in p['sections'] if f"{p['code']}:{z}:{s}" in self._records]
        if not records:
            return None
        votes = {n:sum(r['votes'][n] for r in records) for n in ('13','22')}
        valid = sum(votes.values())
        candidates = tuple(sorted((ExteriorCandidate(name=name,party=party,votes=votes[number],percentage=(100*votes[number]/valid if valid else None))
            for number,name,party in [('13','Lula','PT'),('22','Flávio Bolsonaro','PL')]),key=lambda c:-c.votes))
        counted = sum(any(f"{p['code']}:{z}:{s}" in self._records for z,s in p['sections']) for p in places)
        return ExteriorCountryResult(country=label,candidates=candidates,round=2,
            source_label='Tribunal Superior Eleitoral · boletins de urna oficiais',source_url=records[0]['source_url'],
            locality_code=('country:'+_normal(label)) if country_group else codes[0],
            sections_counted=len(records),sections_total=sum(len(p['sections']) for p in places),
            blank=sum(r['blank'] for r in records),null=sum(r['null'] for r in records),official_bu=True,
            country_group=country_group,locality_codes=codes,localities_counted=counted,localities_total=len(places))

    def _aggregate(self) -> list[ExteriorCountryResult]:
        groups: dict[str,list[str]] = {}
        self._locality_items = []
        for place in self._places:
            groups.setdefault(place['country'],[]).append(place['code'])
            local = self._result(place['name'].title()+' · '+place['country'],(place['code'],),False)
            if local:self._locality_items.append(local)
        items = [self._result(name,tuple(codes),True) for name,codes in sorted(groups.items())]
        return [item for item in items if item is not None]

    async def fetch(self) -> list[ExteriorCountryResult]:
        self.health.update(running=True,last_error=None)
        changed, misses = False,0
        self._requests_left = 120
        self._deadline = time.monotonic()+30
        try:
            await self._load()
            await self._index()
            now = datetime.now(timezone.utc)
            visited = 0
            while visited < len(self._places):
                index = self._cursor % len(self._places)
                place = self._places[index]
                self._cursor = (index+1) % len(self._places)
                visited += 1
                code = place['code']; sections = place['sections']
                if not sections or time.time() < self._probe_after.get(code,0):continue
                if _closed(place['name'],now) is False:continue
                complete = all(f'{code}:{z}:{s}' in self._records for z,s in sections)
                quantum = 1 if complete else min(8,len(sections))
                for _ in range(quantum):
                    current = self._next_section.get(code,0) % len(sections)
                    z,s = sections[current]
                    missing = False
                    try:
                        changed = await self._section(place,z,s) or changed
                    except httpx.HTTPStatusError as error:
                        if error.response.status_code in {403,429}:raise
                        if error.response.status_code in {404,410}:
                            misses += 1
                            missing = True
                        else:
                            self.health['last_error'] = f'HTTP {error.response.status_code} em {place["name"]}'
                    except (ValueError,KeyError,IndexError,UnicodeError,httpx.RequestError) as error:
                        self.health['last_error'] = f'BU pendente em {place["name"]}: {type(error).__name__}'
                        log.warning('BU não incorporado: localidade=%s secao=%s erro=%s',code,s,type(error).__name__)
                    self._consulted.add(code)
                    self._next_section[code] = (current+1) % len(sections)
                    if missing or misses >= 12:break
                self._probe_after[code] = time.time()+(300 if complete else 45)
                if misses >= 12:break
        except ScanBudget:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.health['last_error'] = f'{type(error).__name__}: {str(error)[:160]}'
            log.warning('Monitor exterior: %s',self.health['last_error'])
        finally:
            self._requests_left = None
            if self.storage:
                if changed:
                    await self.storage.set_state(CACHE_KEY,json.dumps({'pleito':3221,'records':self._records},ensure_ascii=False))
                await self.storage.set_state(SCAN_KEY,json.dumps({'cursor':self._cursor,'next_section':self._next_section,
                    'probe_after':self._probe_after,'consulted':sorted(self._consulted)}))
        self._items = self._aggregate()
        self.health.update(last_check_at=datetime.now(timezone.utc).isoformat(),
            localities=len(self._locality_items),sections=sum(i.sections_counted for i in self._locality_items),
            countries_with_bu=len(self._items),localities_consulted=len(self._consulted),
            countries_consulted=len({p['country'] for p in self._places if p['code'] in self._consulted}),scan_cursor=self._cursor)
        return self._items

    def snapshot(self) -> dict:
        def serialize(item):
            return {'code':item.locality_code,'name':item.country,'sections_counted':item.sections_counted,'sections_total':item.sections_total,
                'localities_counted':item.localities_counted,'localities_total':item.localities_total,'locality_codes':list(item.locality_codes),
                'candidates':[{'name':c.name,'party':c.party,'votes':c.votes,'percentage':c.percentage} for c in item.candidates],
                'blank':item.blank,'null':item.null,
                'bulletins':[{'municipality':key.split(':')[0],'zone':key.split(':')[1],'section':key.split(':')[2],
                    'url':record['bu_url'],'sha256':record['sha256']} for key,record in self._records.items() if key.split(':')[0] in item.locality_codes]}
        return {'round':2,'source':'TSE · boletins de urna oficiais','health':self.health,
            'countries':[serialize(i) for i in self._items], 'localities':[serialize(i) for i in self._locality_items],
            'coverage':[{'code':p['code'],'name':p['name'],'country':p['country'],
                'consulted':p['code'] in self._consulted,'sections_total':len(p['sections']),
                'sections_counted':sum(f"{p['code']}:{z}:{s}" in self._records for z,s in p['sections'])} for p in self._places]}

    async def close(self) -> None:
        self.health['running'] = False
        await self.client.aclose()
