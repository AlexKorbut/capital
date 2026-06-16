"""Wallets router (Feature #6) — track public crypto addresses, read-only."""
from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents import runners
from core.db import get_db
from core.deps import get_current_user
from models.user import User
from models.wallet import Wallet
from schemas.wallet import WalletCreate, WalletOut, WalletSyncResult
from services import market
from services import wallets as wallets_service

router = APIRouter(prefix="/wallets", tags=["wallets"])


def _graphs(request: Request):
    graphs = getattr(request.app.state, "graphs", None)
    if graphs is None:
        raise HTTPException(status_code=503, detail="Graph runtime not ready")
    return graphs


async def _wallet_out(w: Wallet) -> WalletOut:
    balance = await wallets_service.fetch_balance(w.chain, w.address)
    usd = None
    if balance is not None:
        price = await market.usd_price_for_crypto(w.chain)
        if price is not None:
            usd = str((balance * price).quantize(Decimal("0.01")))
    return WalletOut(
        id=str(w.id),
        chain=w.chain,
        address=w.address,
        label=w.label,
        balance=str(balance) if balance is not None else None,
        usd_value=usd,
        created_at=w.created_at.isoformat() if w.created_at else None,
    )


@router.get("", response_model=list[WalletOut])
async def list_wallets(
    current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[WalletOut]:
    rows = list(
        await db.scalars(
            select(Wallet).where(Wallet.user_id == current.id).order_by(Wallet.created_at.asc())
        )
    )
    return [await _wallet_out(w) for w in rows]


@router.post("", response_model=WalletOut, status_code=status.HTTP_201_CREATED)
async def add_wallet(
    body: WalletCreate,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WalletOut:
    chain = body.chain.upper()
    if chain not in wallets_service.SUPPORTED_CHAINS:
        raise HTTPException(
            status_code=400,
            detail=f"Поддерживаются сети: {', '.join(wallets_service.SUPPORTED_CHAINS)}",
        )
    wallet = Wallet(
        user_id=current.id, chain=chain, address=body.address.strip(), label=body.label
    )
    db.add(wallet)
    await db.commit()
    await db.refresh(wallet)
    return await _wallet_out(wallet)


@router.delete("/{wallet_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_wallet(
    wallet_id: uuid.UUID,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    wallet = await db.get(Wallet, wallet_id)
    if wallet is None or wallet.user_id != current.id:
        raise HTTPException(status_code=404, detail="Кошелёк не найден")
    await db.delete(wallet)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sync", response_model=WalletSyncResult)
async def sync_to_portfolio(
    request: Request,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WalletSyncResult:
    """Snapshot all tracked wallets into the portfolio (balances are authoritative).

    Runs the standard input graph (validate → enrich → persist) with the wallet
    balances pre-parsed, auto-confirming since on-chain balances need no review.
    """
    rows = list(await db.scalars(select(Wallet).where(Wallet.user_id == current.id)))
    if not rows:
        raise HTTPException(status_code=400, detail="Нет отслеживаемых кошельков.")

    assets = []
    for w in rows:
        balance = await wallets_service.fetch_balance(w.chain, w.address)
        if balance is not None and balance > 0:
            assets.append(wallets_service.balance_to_asset(w.chain, balance, w.address))
    if not assets:
        raise HTTPException(status_code=400, detail="Кошельки пусты или недоступны.")

    graphs = _graphs(request)
    thread_id = str(uuid.uuid4())
    state = {
        "user_id": str(current.id),
        "base_currency": current.base_currency or "USD",
        "input_type": "import",
        "raw_text": "",
        "assets": assets,
    }
    await runners.run_input(graphs, thread_id, state)        # pauses at human_review
    result = await runners.resume_input(graphs, thread_id)   # persist
    if result.get("error") or not result.get("snapshot_id"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Не удалось сохранить")

    total = result.get("total_usd")
    return WalletSyncResult(
        snapshot_id=str(result["snapshot_id"]),
        total_usd=str(total) if total is not None else None,
        wallet_count=len(assets),
    )
