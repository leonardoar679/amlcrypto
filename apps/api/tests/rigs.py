"""The fake providers behind one transport, reached through the real provider service."""

from __future__ import annotations

import httpx2

from app.chains.bitcoin.adapter import build_bitcoin_adapter
from app.chains.bitcoin.chains import BITCOIN_CHAIN
from app.chains.evm.adapter import build_evm_adapter
from app.chains.evm.chains import EVM_CHAINS
from app.chains.tron.adapter import build_tron_adapter
from app.chains.tron.chains import TRON_CHAIN
from app.core.config import Settings
from app.providers.service import ProviderService
from tests.fake_alchemy import FakeAlchemy
from tests.fake_esplora import FakeEsplora
from tests.fake_etherscan import KEY, FakeEtherscan
from tests.fake_trongrid import KEY as TRONGRID_KEY
from tests.fake_trongrid import FakeTronGrid
from tests.helpers import FakeClock, FullJitter

ETHEREUM = EVM_CHAINS["eip155:1"]
BSC = EVM_CHAINS["eip155:56"]
TRON = TRON_CHAIN
BITCOIN = BITCOIN_CHAIN
ALCHEMY_KEY = "alchemy-test-key-abcdef123456"
SECRET = "test-secret-key-that-is-comfortably-over-32-characters"


class Rig:
    def __init__(
        self,
        *,
        etherscan_key=KEY,
        alchemy_key=ALCHEMY_KEY,
        trongrid_key=TRONGRID_KEY,
        paid_plan=False,
        **settings,
    ):
        self.etherscan = FakeEtherscan()
        self.alchemy = FakeAlchemy()
        self.trongrid = FakeTronGrid()
        self.esplora = FakeEsplora()
        config = Settings(
            _env_file=None,
            environment="test",
            secret_key=SECRET,
            etherscan_api_key=etherscan_key,
            alchemy_api_key=alchemy_key,
            trongrid_api_key=trongrid_key,
            etherscan_paid_plan=paid_plan,
            **settings,
        )
        clock = FakeClock()
        self.settings = config
        self.providers = ProviderService.from_settings(
            config,
            transport=httpx2.MockTransport(self.route),
            clock=clock,
            sleep=clock.sleep,
            rng=FullJitter(),
        )

    async def route(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "api.etherscan.io":
            return await self.etherscan(request)
        if request.url.host.endswith("alchemy.com"):
            return await self.alchemy(request)
        if request.url.host == "api.trongrid.io":
            return await self.trongrid(request)
        if request.url.host == "blockstream.info":
            return await self.esplora(request)
        return httpx2.Response(404)

    def adapter(self, chain=ETHEREUM, *, budget=None):
        return build_evm_adapter(
            chain,
            self.providers,
            etherscan_paid_plan=self.settings.etherscan_paid_plan,
            budget=budget,
        )

    def tron_adapter(self, *, budget=None):
        return build_tron_adapter(TRON, self.providers, budget=budget)

    def bitcoin_adapter(self, *, budget=None):
        return build_bitcoin_adapter(BITCOIN, self.providers, budget=budget)
