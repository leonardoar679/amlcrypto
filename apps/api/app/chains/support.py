"""Which chains the platform can read, and how to get the adapter for one."""

from __future__ import annotations

from app.chains.adapter import AccountAdapter
from app.chains.evm.adapter import build_evm_adapter
from app.chains.evm.chains import EvmChain, get_evm_chain
from app.chains.tron.adapter import build_tron_adapter
from app.chains.tron.chains import TronChain, get_tron_chain
from app.providers.budget import RequestBudget
from app.providers.registry import PROVIDERS
from app.providers.service import ProviderService

ReadableChain = EvmChain | TronChain
SUPPORTED = ("ethereum", "bsc", "tron")


def find_chain(name: str) -> ReadableChain | None:
    """A chain this platform can read, by slug or CAIP-2 id; None for anything else."""
    return get_evm_chain(name) or get_tron_chain(name)


def keys_needed(chain: ReadableChain) -> str:
    """The settings that would make this chain readable, for a message."""
    if isinstance(chain, TronChain):
        return "TRONGRID_API_KEY"
    if chain.chain_id in PROVIDERS["etherscan"].chains:
        return "ETHERSCAN_API_KEY or ALCHEMY_API_KEY"
    # Etherscan's free plan does not include this chain, so its key alone does not help.
    return "ALCHEMY_API_KEY (or ETHERSCAN_API_KEY with ETHERSCAN_PAID_PLAN=true)"


def build_adapter(
    chain: ReadableChain,
    providers: ProviderService,
    *,
    etherscan_paid_plan: bool = False,
    budget: RequestBudget | None = None,
) -> AccountAdapter:
    if isinstance(chain, TronChain):
        return build_tron_adapter(chain, providers, budget=budget)
    return build_evm_adapter(
        chain, providers, etherscan_paid_plan=etherscan_paid_plan, budget=budget
    )
