"""Turning Solana transactions into our records, checked against what the chain says balances did.

The input is a transaction as `getTransaction` or `getTransactionsForAddress` return it with the
`jsonParsed` encoding: its instructions, the instructions programs ran inside them (inner
instructions) and `meta`, which holds the fee and every balance before and after.

Two ways of seeing what moved, and the second keeps the first honest:

* the INSTRUCTIONS say who sent what to whom: system transfers, token transfers, mints, burns.
  Each becomes a `Transfer` at an exact position ("3" is the fourth instruction, "3.1" the second
  instruction it called). Value moves between token accounts, so the sender and receiver are the
  wallets that own them, read from the token balances in `meta`.
* the BALANCES say what actually happened. Lamports are replayed through the instructions in the
  order they ran; whatever difference remains between that and the chain's own after-balances
  is reported as unattributed instead of being lost (a program can edit lamports without any
  instruction saying so). The same is done for token balances.

A failed transaction changed nothing but the fee, and is recorded as exactly that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from app.chains.encoding import B58_ALPHABET, b58decode
from app.chains.models import Asset, Confirmation, DataQuality, Transfer, TransferType, TxStatus
from app.chains.solana.address import SolanaAddressValidator
from app.chains.solana.chains import SolanaChain
from app.chains.solana.models import SolanaTransaction, SolChange, TokenChange, Unattributed
from app.chains.values import MalformedRow, raw_amount, to_int

SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TOKEN_PROGRAMS = frozenset({TOKEN_PROGRAM, TOKEN_2022_PROGRAM})
WSOL_MINT = "So11111111111111111111111111111111111111112"
MAX_INSTRUCTIONS = 100_000  # far above any valid transaction; bounds the work on hostile input
MAX_ERROR_TEXT = 160

# Token instructions that move value in a shape this reader does not decode. They are counted, so a
# transaction that uses one is not presented as fully read.
_UNREAD_TOKEN_KINDS = frozenset(
    {
        "transferCheckedWithFee",
        "withdrawWithheldTokensFromAccounts",
        "withdrawWithheldTokensFromMint",
        "harvestWithheldTokensToMint",
        "unwrapLamports",
    }
)
_INITIALIZE_KINDS = frozenset({"initializeAccount", "initializeAccount2", "initializeAccount3"})
_VALIDATOR = SolanaAddressValidator()


# --- scalars -----------------------------------------------------------------------------------


@lru_cache(maxsize=8192)
def solana_address(value: Any) -> str:
    check = _VALIDATOR.check(str(value or ""))
    if not check.valid or check.normalized is None:
        raise MalformedRow(f"not a Solana address: {str(value)[:50]!r}")
    return check.normalized


def signature_of(value: Any) -> str:
    """A transaction signature: 64 bytes in Base58."""
    text = str(value or "").strip()
    if not 64 <= len(text) <= 90 or not B58_ALPHABET.issuperset(text):
        raise MalformedRow(f"not a transaction signature: {text[:90]!r}")
    try:
        if len(b58decode(text)) != 64:
            raise MalformedRow(f"not a transaction signature: {text[:90]!r}")
    except ValueError as error:
        raise MalformedRow(f"not a transaction signature: {text[:90]!r}") from error
    return text


def _moment(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(to_int(value), UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise MalformedRow("timestamp out of range") from error


def _version(value: Any) -> str:
    if value is None or value == "legacy":
        return "legacy"
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    raise MalformedRow(f"unknown transaction version {str(value)[:20]!r}")


def _error_text(error: Any) -> str:
    return json.dumps(error, sort_keys=True)[:MAX_ERROR_TEXT]


def _list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise MalformedRow(f"{what} is not a list")
    return value


# --- token balances ----------------------------------------------------------------------------


@dataclass(slots=True)
class _Token:
    mint: str
    decimals: int | None  # None for an account known only from its initialisation
    owner: str | None
    program_id: str | None
    before: int = 0  # 0 when the account did not exist before the transaction
    after: int = 0  # 0 when it was closed


def _token_accounts(meta: dict[str, Any], accounts: list[str]) -> dict[str, _Token]:
    """Every token account the transaction touched, from its before and after token balances."""
    found: dict[str, _Token] = {}
    for side in ("pre", "post"):
        for entry in _list(meta.get(f"{side}TokenBalances") or [], f"{side}TokenBalances"):
            if not isinstance(entry, dict):
                raise MalformedRow("a token balance that is not an object")
            index = to_int(entry.get("accountIndex"))
            if not 0 <= index < len(accounts):
                raise MalformedRow("a token balance for an account the transaction lacks")
            amount = entry.get("uiTokenAmount")
            if not isinstance(amount, dict):
                raise MalformedRow("a token balance without an amount")
            raw, decimals = int(raw_amount(amount.get("amount"))), to_int(amount.get("decimals"))
            owner = entry.get("owner")
            program = entry.get("programId")
            account = accounts[index]
            token = found.get(account)
            if token is None:
                token = found[account] = _Token(
                    mint=solana_address(entry.get("mint")),
                    decimals=decimals,
                    owner=None if owner in (None, "") else solana_address(owner),
                    program_id=None if program in (None, "") else solana_address(program),
                )
            elif side == "post" and owner not in (None, ""):
                token.owner = solana_address(owner)  # the owner after the transaction wins
            if side == "pre":
                token.before = raw
            else:
                token.after = raw
    return found


def _learn_new_accounts(
    tokens: dict[str, _Token],
    accounts: list[str],
    instructions: list[Any],
    groups: dict[int, list[Any]],
) -> None:
    """Owners of token accounts that exist only during the transaction.

    A swap often creates a temporary wrapped-SOL account, pays into it and closes it again. Such an
    account is in neither the before nor the after token balances, but the instruction that
    initialised it says who owns it.
    """
    decimals = {
        token.mint: token.decimals for token in tokens.values() if token.decimals is not None
    }
    decimals.setdefault(WSOL_MINT, 9)
    every = [*instructions, *(ix for group in groups.values() for ix in group)]
    for ix in every:
        parsed = ix.get("parsed") if isinstance(ix, dict) else None
        if not isinstance(parsed, dict) or parsed.get("type") not in _INITIALIZE_KINDS:
            continue
        info = parsed.get("info")
        program = ix.get("programId")
        if not isinstance(info, dict) or program not in TOKEN_PROGRAMS:
            continue
        account = solana_address(info.get("account"))
        known = tokens.get(account)
        owner = None if info.get("owner") in (None, "") else solana_address(info["owner"])
        if known is None:
            mint = solana_address(info.get("mint"))
            tokens[account] = _Token(mint, decimals.get(mint), owner, solana_address(program))
        elif known.owner is None:
            known.owner = owner


# --- walking the instructions ------------------------------------------------------------------


@dataclass(slots=True)
class _Walk:
    chain: SolanaChain
    signature: str
    slot: int
    timestamp: datetime | None
    transaction_index: int | None
    provider: str
    lamports: dict[str, int]  # replayed through the instructions
    tokens: dict[str, _Token]
    transfers: list[Transfer] = field(default_factory=list)
    token_moved: dict[str, int] = field(
        default_factory=dict
    )  # token account -> net by instructions
    unreadable: int = 0
    unresolved: set[str] = field(default_factory=set)

    def wallet(self, account: str) -> str:
        """The wallet that owns a token account; the account itself when the transaction does not
        say (and that is counted)."""
        token = self.tokens.get(account)
        if token is None or token.owner is None:
            self.unresolved.add(account)
            return account
        return token.owner

    def economic_owner(self, account: str) -> str:
        """The rent in a token account belongs to the wallet that owns the account."""
        token = self.tokens.get(account)
        return token.owner if token is not None and token.owner is not None else account

    def base(self, position: str) -> dict[str, Any]:
        return {
            "chain_id": self.chain.chain_id,
            "transaction_hash": self.signature,
            "event_key": position,
            "block_number": self.slot,
            "timestamp": self.timestamp,
            "provider": self.provider,
            "data_quality": DataQuality.DECODED,
            "confirmation_status": Confirmation.CONFIRMED,
            "transaction_index": self.transaction_index,
            "trace_id": position,
        }


def _sol_move(
    walk: _Walk,
    position: str,
    nested: bool,
    source: str,
    destination: str,
    lamports: int,
    operation: str,
    *,
    map_owners: bool = False,
) -> None:
    if source not in walk.lamports or destination not in walk.lamports:
        walk.unreadable += 1  # an instruction naming an account the transaction does not list
        return
    walk.lamports[source] -= lamports
    walk.lamports[destination] += lamports
    walk.transfers.append(
        Transfer(
            transfer_type=TransferType.INTERNAL if nested else TransferType.NATIVE,
            from_address=walk.economic_owner(source) if map_owners else source,
            to_address=walk.economic_owner(destination) if map_owners else destination,
            asset=walk.chain.native,
            amount_raw=str(lamports),
            source_account=source,
            destination_account=destination,
            program_id=SYSTEM_PROGRAM,
            operation=operation,
            **walk.base(position),
        )
    )


def _system(walk: _Walk, position: str, nested: bool, kind: str, info: dict[str, Any]) -> None:
    if kind in ("transfer", "transferWithSeed"):
        source, destination = (
            solana_address(info.get("source")),
            solana_address(info.get("destination")),
        )
        _sol_move(
            walk, position, nested, source, destination, to_int(info.get("lamports")), "transfer"
        )
    elif kind in ("createAccount", "createAccountWithSeed"):
        source, created = solana_address(info.get("source")), solana_address(info.get("newAccount"))
        _sol_move(
            walk,
            position,
            nested,
            source,
            created,
            to_int(info.get("lamports")),
            "create_account",
            map_owners=True,
        )
    elif kind == "withdrawFromNonce":
        nonce, destination = (
            solana_address(info.get("nonceAccount")),
            solana_address(info.get("destination")),
        )
        _sol_move(
            walk,
            position,
            nested,
            nonce,
            destination,
            to_int(info.get("lamports")),
            "withdraw_nonce",
        )


def _token_amount(info: dict[str, Any]) -> tuple[int, int | None]:
    """(raw amount, decimals when the instruction says): `tokenAmount` for the checked forms."""
    if isinstance(info.get("tokenAmount"), dict):
        amount = info["tokenAmount"]
        return int(raw_amount(amount.get("amount"))), to_int(amount.get("decimals"))
    return int(raw_amount(info.get("amount"))), None


def _spl_transfer(
    walk: _Walk,
    position: str,
    program_id: str,
    operation: str,
    sender: str | None,
    receiver: str | None,
    info: dict[str, Any],
) -> None:
    """One token movement: `sender` or `receiver` is a token account, None for a mint or a burn."""
    amount, decimals = _token_amount(info)
    account = sender or receiver
    token = walk.tokens.get(account) if account else None
    mint = info.get("mint")
    if mint is None and token is not None:
        mint = token.mint
    if decimals is None and token is not None:
        decimals = token.decimals
    if mint is None or decimals is None:
        walk.unreadable += 1  # no way to tell which token this is
        return
    if sender:
        walk.token_moved[sender] = walk.token_moved.get(sender, 0) - amount
    if receiver:
        walk.token_moved[receiver] = walk.token_moved.get(receiver, 0) + amount
    if (
        mint == WSOL_MINT
        and sender
        and receiver
        and sender in walk.lamports
        and receiver in walk.lamports
    ):
        # Wrapped SOL is lamports held in a token account: moving it moves them.
        walk.lamports[sender] -= amount
        walk.lamports[receiver] += amount
    walk.transfers.append(
        Transfer(
            transfer_type=TransferType.SPL,
            from_address=walk.wallet(sender) if sender else None,
            to_address=walk.wallet(receiver) if receiver else None,
            asset=Asset(symbol=None, contract_address=solana_address(mint), decimals=decimals),
            amount_raw=str(amount),
            source_account=sender,
            destination_account=receiver,
            program_id=program_id,
            operation=operation,
            **walk.base(position),
        )
    )


def _token(
    walk: _Walk, position: str, nested: bool, program_id: str, kind: str, info: dict[str, Any]
) -> None:
    if kind in ("transfer", "transferChecked"):
        sender, receiver = (
            solana_address(info.get("source")),
            solana_address(info.get("destination")),
        )
        _spl_transfer(walk, position, program_id, "transfer", sender, receiver, info)
    elif kind in ("mintTo", "mintToChecked"):
        _spl_transfer(
            walk, position, program_id, "mint", None, solana_address(info.get("account")), info
        )
    elif kind in ("burn", "burnChecked"):
        _spl_transfer(
            walk, position, program_id, "burn", solana_address(info.get("account")), None, info
        )
    elif kind == "closeAccount":
        # The lamports left in a closed token account (its rent, and any wrapped SOL) go to the
        # destination. How many is whatever the replay says the account holds at that moment.
        closed, destination = (
            solana_address(info.get("account")),
            solana_address(info.get("destination")),
        )
        held = walk.lamports.get(closed)
        if held:
            _sol_move(
                walk, position, nested, closed, destination, held, "close_account", map_owners=True
            )
    elif kind in _UNREAD_TOKEN_KINDS:
        walk.unreadable += 1


def _instruction(walk: _Walk, ix: Any, position: str, nested: bool) -> None:
    if not isinstance(ix, dict):
        raise MalformedRow("an instruction that is not an object")
    parsed = ix.get("parsed")
    if not isinstance(parsed, dict):
        return  # a program with no parser (or a memo): nothing here that we can read
    kind, info = parsed.get("type"), parsed.get("info")
    if not isinstance(kind, str) or not isinstance(info, dict):
        return
    program_id = solana_address(ix.get("programId"))
    if program_id == SYSTEM_PROGRAM:
        _system(walk, position, nested, kind, info)
    elif program_id in TOKEN_PROGRAMS:
        _token(walk, position, nested, program_id, kind, info)


# --- the transaction ---------------------------------------------------------------------------


def solana_transaction(
    item: Any, chain: SolanaChain, provider: str = "helius"
) -> SolanaTransaction:
    """A transaction as getTransaction or getTransactionsForAddress return it (jsonParsed)."""
    if not isinstance(item, dict):
        raise MalformedRow("a transaction that is not an object")
    tx, meta = item.get("transaction"), item.get("meta")
    if not isinstance(tx, dict) or not isinstance(meta, dict):
        raise MalformedRow("a transaction without its message or its meta")
    signature = signature_of((_list(tx.get("signatures"), "signatures") or [None])[0])
    message = tx.get("message")
    if not isinstance(message, dict):
        raise MalformedRow("a transaction without a message")

    keys = _list(message.get("accountKeys"), "accountKeys")
    if not keys or not all(isinstance(key, dict) for key in keys):
        raise MalformedRow("account keys must be in the jsonParsed form")
    accounts = [solana_address(key.get("pubkey")) for key in keys]
    signers = tuple(solana_address(key["pubkey"]) for key in keys if key.get("signer") is True)
    pre = [int(raw_amount(value)) for value in _list(meta.get("preBalances"), "preBalances")]
    post = [int(raw_amount(value)) for value in _list(meta.get("postBalances"), "postBalances")]
    if not len(pre) == len(post) == len(accounts):
        raise MalformedRow("balances do not match the accounts")

    instructions = _list(message.get("instructions"), "instructions")
    groups = {
        to_int(group.get("index")): _list(group.get("instructions"), "inner instructions")
        for group in _list(meta.get("innerInstructions") or [], "innerInstructions")
        if isinstance(group, dict)
    }
    if len(instructions) + sum(len(group) for group in groups.values()) > MAX_INSTRUCTIONS:
        raise MalformedRow("a transaction with an absurd number of instructions")

    failed = meta.get("err") is not None
    fee = int(raw_amount(meta.get("fee")))
    fee_payer = accounts[0]
    slot = to_int(item.get("slot"))
    if slot < 0:
        raise MalformedRow("negative slot")
    index = item.get("transactionIndex")
    tokens = _token_accounts(meta, accounts)
    _learn_new_accounts(tokens, accounts, instructions, groups)
    walk = _Walk(
        chain=chain,
        signature=signature,
        slot=slot,
        timestamp=_moment(item.get("blockTime")),
        transaction_index=None if index is None else to_int(index),
        provider=provider,
        lamports=dict(zip(accounts, pre, strict=True)),
        tokens=tokens,
    )

    if not failed:  # a failed transaction changed nothing but the fee, whatever it started to do
        for number, ix in enumerate(instructions):
            _instruction(walk, ix, str(number), nested=False)
            for inner_number, inner in enumerate(groups.get(number, ())):
                _instruction(walk, inner, f"{number}.{inner_number}", nested=True)

    unattributed: list[Unattributed] = []
    for account, after in zip(accounts, post, strict=True):
        expected = walk.lamports[account] - (fee if account == fee_payer else 0)
        if after != expected:
            unattributed.append(Unattributed("sol", account, None, None, str(after - expected)))
    for account, token in walk.tokens.items():
        if token.mint == WSOL_MINT:
            continue  # wrapped SOL is accounted for in lamports
        residual = token.after - token.before - walk.token_moved.get(account, 0)
        if residual:
            unattributed.append(
                Unattributed("token", account, token.owner, token.mint, str(residual))
            )

    programs: dict[str, None] = {}
    for ix in instructions:
        if isinstance(ix, dict) and ix.get("programId"):
            programs.setdefault(solana_address(ix["programId"]))

    return SolanaTransaction(
        chain_id=chain.chain_id,
        signature=signature,
        slot=slot,
        timestamp=walk.timestamp,
        version=_version(item.get("version")),
        status=TxStatus.FAILED if failed else TxStatus.SUCCESS,
        error=_error_text(meta["err"]) if failed else None,
        fee_raw=str(fee),
        fee_payer=fee_payer,
        signers=signers,
        programs=tuple(programs),
        transfers=tuple(walk.transfers),
        sol_changes=tuple(
            SolChange(account, str(before), str(after))
            for account, before, after in zip(accounts, pre, post, strict=True)
            if before != after
        ),
        token_changes=tuple(
            TokenChange(
                account,
                token.owner,
                token.mint,
                token.decimals or 0,
                token.program_id,
                str(token.before),
                str(token.after),
            )
            for account, token in walk.tokens.items()
            if token.before != token.after
        ),
        unattributed=tuple(unattributed),
        unreadable=walk.unreadable,
        provider=provider,
        data_quality=DataQuality.DECODED,
        transaction_index=walk.transaction_index,
        unresolved_owners=len(walk.unresolved),
    )
