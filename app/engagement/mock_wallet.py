"""In-memory Mock Wallet Service conforming to Pod Alpha contract.

Module 4: Arena - Pod Beta.
Follows strict zero em dash rule: only hyphens (-) or colons (:).
Conforms to PRD Section 3.1 & 11.5 for Zero Interdependency build model.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional
from pydantic import BaseModel


class WalletInsufficientCreditsError(Exception):
    """Raised when tenant wallet does not have sufficient credits for debit."""

    def __init__(self, cost: int, balance: int):
        self.cost = cost
        self.balance = balance
        self.status_code = 402
        super().__init__(f"Need {cost} credits, have {balance}")


class LedgerEntry(BaseModel):
    """Transaction record in the double-entry ledger."""
    entry_id: str
    tenant_id: str
    entry_type: str  # 'debit' or 'credit'
    amount: int
    balance_after: int
    idempotency_key: str
    reason: str
    module_source: str = "mod4"
    timestamp: datetime


class MockWalletClient:
    """In-memory mock wallet client implementing the Wallet API contract."""

    def __init__(self, seed_tenants: bool = True):
        self._balances: Dict[str, int] = {}
        self._entries: List[LedgerEntry] = []
        self._idempotency: Dict[str, LedgerEntry] = {}

        if seed_tenants:
            # Seed five tenant wallets per PRD Section 3.1 & 11.7
            self._balances = {
                "tenant_zero": 0,
                "tenant_low": 10,
                "tenant_medium": 50,
                "tenant_high": 200,
                "tenant_whale": 999999,
                # Also generic aliases
                "tenant_1": 0,
                "tenant_2": 10,
                "tenant_3": 50,
                "tenant_4": 200,
                "tenant_5": 999999,
            }

    async def get_balance(self, tenant_id: str) -> int:
        """Fetch current available wallet balance for a tenant."""
        return self._balances.get(tenant_id, 0)

    async def debit(
        self,
        tenant_id: str,
        amount: int,
        idempotency_key: str,
        reason: str,
        module_source: str = "mod4",
    ) -> LedgerEntry:
        """Debit credits from tenant wallet with full idempotency guarantee.

        Raises WalletInsufficientCreditsError (HTTP 402) if balance < amount.
        """
        # Idempotency check: duplicate key returns original result
        if idempotency_key in self._idempotency:
            return self._idempotency[idempotency_key]

        current_balance = self._balances.get(tenant_id, 0)
        if current_balance < amount:
            raise WalletInsufficientCreditsError(cost=amount, balance=current_balance)

        new_balance = current_balance - amount
        self._balances[tenant_id] = new_balance

        entry_id = hashlib.sha256(f"{tenant_id}{idempotency_key}".encode()).hexdigest()[:16]
        entry = LedgerEntry(
            entry_id=f"debit-{entry_id}",
            tenant_id=tenant_id,
            entry_type="debit",
            amount=amount,
            balance_after=new_balance,
            idempotency_key=idempotency_key,
            reason=reason,
            module_source=module_source,
            timestamp=datetime.now(timezone.utc),
        )

        self._entries.append(entry)
        self._idempotency[idempotency_key] = entry
        return entry

    async def credit(
        self,
        tenant_id: str,
        amount: int,
        idempotency_key: str,
        reason: str,
        module_source: str = "mod4",
    ) -> LedgerEntry:
        """Credit credits to tenant wallet (e.g. refund, purchase, subscription)."""
        if idempotency_key in self._idempotency:
            return self._idempotency[idempotency_key]

        current_balance = self._balances.get(tenant_id, 0)
        new_balance = current_balance + amount
        self._balances[tenant_id] = new_balance

        entry_id = hashlib.sha256(f"{tenant_id}{idempotency_key}".encode()).hexdigest()[:16]
        entry = LedgerEntry(
            entry_id=f"credit-{entry_id}",
            tenant_id=tenant_id,
            entry_type="credit",
            amount=amount,
            balance_after=new_balance,
            idempotency_key=idempotency_key,
            reason=reason,
            module_source=module_source,
            timestamp=datetime.now(timezone.utc),
        )

        self._entries.append(entry)
        self._idempotency[idempotency_key] = entry
        return entry

    def set_balance(self, tenant_id: str, balance: int) -> None:
        """Directly configure balance for testing scenarios."""
        self._balances[tenant_id] = balance

    def get_entries(self, tenant_id: Optional[str] = None) -> List[LedgerEntry]:
        """Fetch all ledger entries, optionally filtered by tenant."""
        if tenant_id:
            return [e for e in self._entries if e.tenant_id == tenant_id]
        return list(self._entries)
