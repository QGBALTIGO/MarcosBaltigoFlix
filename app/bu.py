"""Read the bounded ASN.1/BER structures used by TSE BU v2.

Only the official presidential second round is accepted. This checks identity,
dates and vote accounting; it does not claim to verify the electronic signature.
The wire format is documented in the TSE's 2026 BU/RDV specification.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Node:
    tag: int
    value: bytes
    children: tuple['Node', ...] = ()

    def number(self, tag: int = 2) -> int:
        if self.tag != tag or not 1 <= len(self.value) <= 8 or self.value[0] & 128:
            raise ValueError('Invalid BU integer')
        return int.from_bytes(self.value, 'big')


def _nodes(raw: bytes, depth: int = 0) -> tuple[Node, ...]:
    if depth > 20:
        raise ValueError('BU nesting limit')
    offset, result = 0, []
    while offset < len(raw):
        if offset + 2 > len(raw):
            raise ValueError('Truncated BU')
        tag, length = raw[offset:offset + 2]
        offset += 2
        if tag & 31 == 31 or length == 128:
            raise ValueError('Unsupported BU encoding')
        if length & 128:
            count = length & 127
            if count > 4 or offset + count > len(raw):
                raise ValueError('Invalid BU length')
            length = int.from_bytes(raw[offset:offset + count], 'big')
            offset += count
        end = offset + length
        if end > len(raw):
            raise ValueError('Truncated BU value')
        value = raw[offset:end]
        result.append(Node(tag, value, _nodes(value, depth + 1) if tag & 32 else ()))
        offset = end
        if len(result) > 1000:
            raise ValueError('BU element limit')
    return tuple(result)


def _sequence(node: Node, tag: int = 48) -> tuple[Node, ...]:
    if node.tag != tag:
        raise ValueError('Unexpected BU structure')
    return node.children


def _header(node: Node) -> None:
    fields = _sequence(node)
    if len(fields) != 2 or fields[1].number(130) != 3221:
        raise ValueError('BU belongs to another election day')
    _date(fields[0])


def _date(node: Node) -> str:
    if node.tag != 27:
        raise ValueError('Invalid BU date')
    value = node.value.decode('ascii')
    if datetime.strptime(value, '%Y%m%dT%H%M%S').date().isoformat() != '2026-10-25':
        raise ValueError('BU belongs to another date')
    return value


def _identity(node: Node, tag: int = 48) -> tuple[int, int, int]:
    fields = _sequence(node, tag)
    municipality_zone = _sequence(fields[0])
    if len(fields) != 3 or len(municipality_zone) != 2:
        raise ValueError('Invalid BU section identity')
    return municipality_zone[0].number(), municipality_zone[1].number(), fields[2].number()


def parse_presidential_bu(raw: bytes, municipality: str, zone: str, section: str) -> dict:
    if not 100 <= len(raw) <= 512_000:
        raise ValueError('Invalid BU size')
    roots = _nodes(raw)
    if len(roots) != 1:
        raise ValueError('Trailing BU data')
    envelope = _sequence(roots[0])
    if len(envelope) != 5:
        raise ValueError('Unsupported BU envelope')
    _header(envelope[0])
    expected = int(municipality), int(zone), int(section)
    if envelope[1].number(10) != 2 or envelope[3].number(10) != 1:
        raise ValueError('Not an official BU')
    if _identity(envelope[2], 160) != expected or envelope[4].tag != 4:
        raise ValueError('BU section differs from requested section')
    contents = _nodes(envelope[4].value)
    if len(contents) != 1:
        raise ValueError('Invalid BU content')
    body = _sequence(contents[0])
    if len(body) < 9:
        raise ValueError('Incomplete BU')
    _header(body[0])
    if body[1].number(10) != 2 or _identity(body[3]) != expected:
        raise ValueError('BU content identity or phase mismatch')
    emitted = _date(body[4])
    attendance = body[6].number()
    # Optional v2 breakdown of attendance precedes the election results.
    index = 8 if body[7].tag == 161 else 7
    elections = _sequence(body[index])
    if len(elections) != 1:
        raise ValueError('Exterior BU must contain one presidential election')
    election = _sequence(elections[0])
    if len(election) != 7 or election[0].number() != 6258:
        raise ValueError('BU belongs to another election or round')
    eligible = election[1].number()
    if election[2].number() + election[3].number() != eligible:
        raise ValueError('BU electorate does not balance')
    results = _sequence(election[4])
    if len(results) != 1:
        raise ValueError('Unexpected BU vote groups')
    result = _sequence(results[0])
    if result[0].number(10) != 1 or result[1].number() != attendance:
        raise ValueError('BU attendance mismatch')
    offices = _sequence(result[2])
    if len(offices) != 1:
        raise ValueError('Unexpected BU offices')
    office = _sequence(offices[0])
    if office[0].number(129) != 1:
        raise ValueError('BU is not presidential')
    votes, blank, null, seen = {'13': 0, '22': 0}, 0, 0, set()
    for node in _sequence(office[2]):
        fields = _sequence(node)
        kind, count = fields[0].number(129), fields[1].number(130)
        if kind == 1:
            identity = _sequence(fields[2], 163)
            number = str(identity[1].number())
            if number not in votes or identity[0].number() != int(number) or number in seen:
                raise ValueError('Unexpected or duplicate second-round candidate')
            votes[number] = count
            seen.add(number)
        elif kind in {2, 3} and kind not in seen and not any(f.tag == 163 for f in fields):
            seen.add(kind)
            if kind == 2:
                blank = count
            else:
                null = count
        else:
            raise ValueError('Unexpected BU vote type')
    if sum(votes.values()) + blank + null != attendance or not 0 <= attendance <= eligible:
        raise ValueError('BU votes do not balance with attendance')
    return {'votes': votes, 'blank': blank, 'null': null, 'attendance': attendance,
            'eligible': eligible, 'emitted_at': emitted}
