import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiorpcx import NetAddress

from electrumx.lib.coins import Namecoin
from electrumx.server.db import DB
from electrumx.server.session import NameIndexAuxPoWElectrumX, SessionManager


class MockSession:
    def __init__(self, remote_address):
        self._remote_address = remote_address

    def remote_address(self):
        return self._remote_address


def make_session_manager():
    manager = SessionManager.__new__(SessionManager)
    manager.env = SimpleNamespace(
        session_group_by_subnet_ipv4=24,
        session_group_by_subnet_ipv6=64,
    )
    manager.session_event = asyncio.Event()
    manager.sessions = {}
    manager.session_groups = {}
    return manager


@pytest.mark.parametrize('host, expected', (
    (None, 'unknown_addr'),
    ('8.8.8.8', '8.8.8.0/24'),
    ('192.168.1.2', None),
    ('2001:4860:4860::8888', '2001:4860:4860::/64'),
    ('fd00::1', None),
))
def test_ip_addr_group_name(host, expected):
    manager = make_session_manager()
    remote_address = NetAddress(host, 50001) if host is not None else None

    session = MockSession(remote_address)

    assert manager._ip_addr_group_name(session) == expected


def test_add_session_without_remote_address_uses_unknown_group():
    manager = make_session_manager()
    session = MockSession(None)

    manager.add_session(session)

    group = manager.session_groups['unknown_addr']
    assert manager.sessions[session] == (group,)
    assert group.sessions == {session}


_HEADERS = (b'G' * 80, b'A' * 240, b'B' * 320, b'C' * 80)


@pytest.fixture
def header_db():
    db = DB.__new__(DB)
    db.coin = Namecoin
    db.db_height = 3
    db.dynamic_header_offset = Mock(side_effect={
        0: 0, 1: 80, 2: 320, 3: 640, 4: 720,
    }.__getitem__)
    db.header_offset = db.dynamic_header_offset
    db.header_len = db.dynamic_header_len
    raw = b''.join(_HEADERS)
    db.headers_file = SimpleNamespace(
        read=lambda offset, size: raw[offset:offset + size],
    )
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize('start_height, count, expected', (
    (0, 2, [_HEADERS[0], _HEADERS[1]]),
    (1, 2, [_HEADERS[1], _HEADERS[2]]),
    (3, 5, [_HEADERS[3]]),
    (4, 2, []),
    (1, 0, []),
))
async def test_read_headers_array(header_db, start_height, count, expected):
    assert await header_db.read_headers_array(start_height, count) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol, cp_height, payload', (
    ((1, 4, 3), 0, {'hex': (_HEADERS[1] + _HEADERS[2]).hex()}),
    ((1, 4, 3), 3, {'hex': (b'A' * 80 + b'B' * 80).hex()}),
    ((1, 6), 0, {'headers': [_HEADERS[1].hex(), _HEADERS[2].hex()]}),
    ((1, 6), 3, {'headers': [(b'A' * 80).hex(), (b'B' * 80).hex()]}),
))
async def test_namecoin_header_batches(header_db, protocol, cp_height, payload):
    session = NameIndexAuxPoWElectrumX.__new__(NameIndexAuxPoWElectrumX)
    session.db = header_db
    session.coin = Namecoin
    session.protocol_tuple = protocol
    session.bump_cost = Mock()
    proof = {'branch': [], 'root': '11' * 32}
    session._merkle_proof = AsyncMock(return_value=proof)

    result = await session.block_headers(1, 2, cp_height)

    expected = {'count': 2, 'max': session.MAX_CHUNK_SIZE, **payload}
    if cp_height:
        expected.update(proof)
        session._merkle_proof.assert_awaited_once_with(cp_height, 2)
    else:
        session._merkle_proof.assert_not_awaited()
    assert result == expected
