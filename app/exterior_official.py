"""Official second-round BUs, with persistent section accounting and bounded polling."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, available_timezones

import httpx

from .bu import parse_presidential_bu
from .config import Settings
from .exterior import ExteriorCandidate, ExteriorCountryResult
from .storage import Storage

log = logging.getLogger(__name__)
BASE = 'https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3221'
INDEX = BASE + '/config/zz/zz-p003221-cs.json'
CACHE_KEY = 'exterior:official:3221:sections:v1'
COUNTRIES = {'29491':'Austrália', '29742':'Japão', '30627':'Japão', '30198':'Japão'}


def _normal(value: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', value) if c.isascii() and c.isalnum()).lower()


# Time zones are only scheduling hints. Votes and coverage always come from BUs.
ZONE_HINTS = { _normal(k): v for k, v in {
    'CAMBERRA': 'Australia/Sydney', 'SYDNEY': 'Australia/Sydney',
    'MELBOURNE': 'Australia/Melbourne', 'BRISBANE': 'Australia/Brisbane',
    'PERTH': 'Australia/Perth', 'WELLINGTON': 'Pacific/Auckland',
    'AUCKLAND': 'Pacific/Auckland', 'TÓQUIO': 'Asia/Tokyo',
    'NAGÓIA': 'Asia/Tokyo', 'HAMAMATSU': 'Asia/Tokyo',
    'PEQUIM': 'Asia/Shanghai', 'XANGAI': 'Asia/Shanghai',
    'HONG KONG': 'Asia/Hong_Kong', 'SEUL': 'Asia/Seoul',
    'SINGAPURA': 'Asia/Singapore', 'CINGAPURA': 'Asia/Singapore',
    'TAIPÉ': 'Asia/Taipei', 'TAIPEI': 'Asia/Taipei',
    'BANGCOC': 'Asia/Bangkok', 'HANÓI': 'Asia/Bangkok',
    'JACARTA': 'Asia/Jakarta', 'NOVA DÉLHI': 'Asia/Kolkata',
    'NOVA DELHI': 'Asia/Kolkata', 'KATMANDU': 'Asia/Kathmandu',
    'MOSCOU': 'Europe/Moscow', 'LISBOA': 'Europe/Lisbon',
    'PORTO': 'Europe/Lisbon', 'LONDRES': 'Europe/London',
    'ROMA': 'Europe/Rome', 'MILÃO': 'Europe/Rome',
    'MUNIQUE': 'Europe/Berlin', 'FRANKFURT': 'Europe/Berlin',
    'BERLIM': 'Europe/Berlin', 'BRUXELAS': 'Europe/Brussels',
    'ATENAS': 'Europe/Athens', 'ESTOCOLMO': 'Europe/Stockholm',
    'COPENHAGUE': 'Europe/Copenhagen', 'VARSÓVIA': 'Europe/Warsaw',
    'VIENA': 'Europe/Vienna', 'ZURIQUE': 'Europe/Zurich',
    'GENEBRA': 'Europe/Zurich', 'NICÓSIA': 'Asia/Nicosia',
    'TEERÃ': 'Asia/Tehran', 'RABAT': 'Africa/Casablanca',
    'CAIRO': 'Africa/Cairo', 'SÃO JOSÉ': 'America/Costa_Rica',
    'NOVA YORK': 'America/New_York', 'LOS ANGELES': 'America/Los_Angeles',
    'SÃO FRANCISCO': 'America/Los_Angeles', 'HARTFORD': 'America/New_York',
    'BOSTON': 'America/New_York', 'WASHINGTON': 'America/New_York',
    'HOUSTON': 'America/Chicago', 'ATLANTA': 'America/New_York',
    'MIAMI': 'America/New_York', 'ORLANDO': 'America/New_York',
}.items() }
for zone in sorted(available_timezones()):
    if zone.startswith(('Africa/', 'America/', 'Asia/', 'Australia/', 'Europe/', 'Pacific/')):
        ZONE_HINTS.setdefault(_normal(zone.rsplit('/', 1)[-1]), zone)


def _closed(name: str, now: datetime) -> bool | None:
    zone = ZONE_HINTS.get(_normal(name))
    if not zone:
        return None
    local = now.astimezone(ZoneInfo(zone))
    return (local.year, local.month, local.day, local.hour) >= (2026, 10, 25, 17)


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
        self._cursor = 0
        self._items: list[ExteriorCountryResult] = []
        self.health = {'running': False, 'source':'TSE · BU oficial · 2º turno',
                       'last_check_at':None, 'last_error':None, 'localities':0, 'sections':0}

    async def _get(self, url: str) -> httpx.Response:
        async with self._rate_lock:
            if time.time() < self._pause_until:
                raise RuntimeError('TSE polling is in backoff')
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
        self._loaded = True

    async def _index(self) -> None:
        if self._places and time.time() - self._index_checked < 600:
            return
        response = await self._get(INDEX)
        data = response.json()
        if str(data.get('f', '')).lower() != 'o' or str(data.get('cdp')) != '3221':
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
                            continue  # Its votes are in the principal section BU.
                        z, s = str(zone['cd']).zfill(4), str(section['ns']).zfill(4)
                        if not re.fullmatch(r'\d{4}', z) or not re.fullmatch(r'\d{4}', s):
                            raise ValueError('Invalid TSE section')
                        sections.append((z, s))
                places.append({'code':code, 'name':place['nm'], 'sections':sorted(set(sections))})
        if not places:
            raise ValueError('TSE index has no exterior localities')
        priority = {'29491':0, '29742':1, '30627':2, '30198':3}
        self._places = sorted(places, key=lambda p:(priority.get(p['code'], 4), p['name']))
        self._index_checked = time.time()

    async def _section(self, place: dict, zone: str, section: str) -> bool:
        code = place['code']
        key = f'{code}:{zone}:{section}'
        folder = f'{BASE}/dados/zz/{code}/{zone}/{section}'
        aux_url = f'{folder}/p003221-zz-m{code}-z{zone}-s{section}-aux.json'
        aux = (await self._get(aux_url)).json()
        if str(aux.get('f','')).lower() != 'o' or aux.get('dg') != '25/10/2026':
            raise ValueError('Wrong exterior auxiliary phase or date')
        if _normal(aux.get('st','')) not in {'recebida', 'totalizada'}:
            return bool(self._records.pop(key, None))
        hashes = sorted(aux.get('hashes', []), key=lambda h:(h.get('dr',''),h.get('hr','')), reverse=True)
        selected = next((h for h in hashes if _normal(h.get('st','')) in {'recebido','totalizado'}), None)
        if not selected or selected.get('dr') != '25/10/2026':
            raise ValueError('No accepted second-round BU hash')
        hash_ = selected['hash']
        if not re.fullmatch('[a-fA-F0-9]{16,256}', hash_):
            raise ValueError('Invalid TSE path hash')
        if self._records.get(key, {}).get('hash') == hash_:
            return False
        file = next((f['nm'] for f in selected.get('arq',[]) if f.get('tp')=='bu'), None)
        if not file or not re.fullmatch(rf'o03221zz{code}{zone}{section}-bu(?:sa)?\.dat', file):
            raise ValueError('Missing or invalid BU filename')
        url = f'{folder}/{hash_}/{file}'
        raw = (await self._get(url)).content
        record = parse_presidential_bu(raw, code, zone, section)
        record.update(hash=hash_, source_url=aux_url, bu_url=url, sha256=hashlib.sha256(raw).hexdigest())
        self._records[key] = record
        return True

    def _aggregate(self) -> list[ExteriorCountryResult]:
        items = []
        for place in self._places:
            records = [self._records[f"{place['code']}:{z}:{s}"] for z,s in place['sections'] if f"{place['code']}:{z}:{s}" in self._records]
            if not records:
                continue
            votes = {n:sum(r['votes'][n] for r in records) for n in ('13','22')}
            valid = sum(votes.values())
            if not valid:
                continue
            rows = tuple(sorted((ExteriorCandidate(name=name, party=party, votes=votes[number], percentage=100*votes[number]/valid)
                for number, name, party in [('13','Lula','PT'),('22','Flávio Bolsonaro','PL')]), key=lambda c:-c.votes))
            label = place['name'].title()
            if place['code'] in COUNTRIES:
                label += ' · ' + COUNTRIES[place['code']]
            items.append(ExteriorCountryResult(country=label, candidates=rows,
                source_label='Tribunal Superior Eleitoral · boletins de urna oficiais', source_url=records[0]['source_url'],
                locality_code=place['code'], round=2, sections_counted=len(records), sections_total=len(place['sections']),
                blank=sum(r['blank'] for r in records), null=sum(r['null'] for r in records), official_bu=True))
        return items

    async def fetch(self) -> list[ExteriorCountryResult]:
        self.health['running'] = True
        self.health['last_error'] = None
        changed = False
        try:
            await self._load()
            await self._index()
            now = datetime.now(timezone.utc)
            unknown = [p for p in self._places if _closed(p['name'],now) is None]
            selected_unknown = unknown[self._cursor % len(unknown)]['code'] if unknown else None
            self._cursor += 1
            checked = 0
            # Unmapped cities are probed after places whose local voting has
            # closed, so a still-unpublished city cannot delay Asia/Oceania.
            scheduled = [p for p in self._places if _closed(p['name'],now) is True]
            scheduled += [p for p in unknown if p['code']==selected_unknown]
            # Each locality is checked as a group; one missing section backs off
            # that locality, avoiding a burst of 404s against the TSE CDN.
            for place in scheduled:
                code = place['code']
                if time.time() < self._probe_after.get(code,0):
                    continue
                try:
                    pending = list(place['sections'])
                    if pending and not any(key.startswith(code+':') for key in self._records):
                        z,s = pending.pop(0)
                        changed = await self._section(place,z,s) or changed
                        checked += 1
                    for start in range(0,len(pending),4):
                        batch = pending[start:start+4]
                        outcomes = await asyncio.gather(*(self._section(place,z,s) for z,s in batch), return_exceptions=True)
                        changed = any(value is True for value in outcomes) or changed
                        checked += len(batch)
                        for outcome in outcomes:
                            if isinstance(outcome, BaseException):
                                raise outcome
                    self._probe_after[code] = time.time() + 300
                except httpx.HTTPStatusError as error:
                    if error.response.status_code in {404,410}:
                        self._probe_after[code] = time.time() + 300
                        # Limit discovery misses, rather than sweeping unopened urns.
                        break
                    raise
                except (ValueError, KeyError, IndexError, UnicodeError) as error:
                    self._probe_after[code] = time.time() + 300
                    log.warning('BU rejeitado na localidade %s: %s', code, type(error).__name__)
                    self.health['last_error'] = f'BU rejeitado em {place["name"]}: {type(error).__name__}'
                if checked >= 160:
                    break
            self.health['last_check_at'] = now.isoformat()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.health['last_error'] = f'{type(error).__name__}: {str(error)[:160]}'
            log.warning('Monitor exterior: %s', self.health['last_error'])
        finally:
            if changed and self.storage:
                await self.storage.set_state(CACHE_KEY, json.dumps({'pleito':3221,'records':self._records}, ensure_ascii=False))
        self._items = self._aggregate()
        self.health.update(localities=len(self._items), sections=sum(i.sections_counted for i in self._items))
        return self._items

    def snapshot(self) -> dict:
        return {'round':2,'source':'TSE · boletins de urna oficiais', 'health':self.health,
            'localities':[{'code':i.locality_code,'name':i.country,'sections_counted':i.sections_counted,'sections_total':i.sections_total,
                'candidates':[{'name':c.name,'party':c.party,'votes':c.votes,'percentage':c.percentage} for c in i.candidates],
                'blank':i.blank,'null':i.null,
                'bulletins':[{'zone':key.split(':')[1],'section':key.split(':')[2], 'url':record['bu_url'],'sha256':record['sha256']}
                    for key,record in self._records.items() if key.startswith(i.locality_code+':')]}
                for i in self._items]}

    async def close(self) -> None:
        self.health['running'] = False
        await self.client.aclose()
