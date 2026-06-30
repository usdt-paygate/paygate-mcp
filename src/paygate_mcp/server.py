"""
PayGate MCP Server

Exposes PayGate payment operations as tools for AI assistants (Claude, etc.).
Claude Desktop runs this as a local subprocess — no server or Docker needed.

Configuration (environment variables):
  PAYGATE_URL     — Your PayGate instance URL  (e.g. http://localhost:9000)
  PAYGATE_API_KEY — Your API key from the dashboard → Integration

Claude Desktop setup (~/.config/claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "paygate": {
        "command": "uvx",
        "args": ["--from", "git+https://github.com/usdt-paygate/paygate-mcp.git", "paygate-mcp"],
        "env": {
          "PAYGATE_URL": "http://your-paygate-url",
          "PAYGATE_API_KEY": "your-api-key"
        }
      }
    }
  }
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from paygate import PayGateClient, PayGateError, PaymentNotFound

mcp = FastMCP(
    "openbcp",
    instructions="""
You have access to an openbcp USDT BEP20 payment gateway via these tools.

openbcp lets merchants accept USDT on Binance Smart Chain (BEP-20).

KEY FACTS:
- Payments need 3 on-chain confirmations by default (~9 seconds after the tx lands)
- Invoices expire after the configured window (default 24 hours)
- Always pass amount_usdt as a string (e.g. "29.99"), never a float
- A platform fee is automatically added to the gross invoice amount

PAYMENT STATUS LIFECYCLE:
  UNPAID    → Waiting. No transaction detected yet.
  PARTIAL   → A confirmed transaction exists but is below the required amount.
              The checkout page shows the shortfall and prompts the customer to
              top up to the same address. The expiry window auto-extends 30 min.
              Do NOT fulfil the order — wait for PAID or OVERPAID.
  PAID      → Full or near-full payment confirmed (98%–105% of expected).
              Fulfil the order.
  OVERPAID  → More than 105% of expected amount received. Fulfil the order.
              The overpaid_by field tells you how much extra was sent.
  EXPIRED   → Invoice timed out. If amount_received > 0, the customer sent
              funds but didn't complete payment — consider issuing a refund.
  CANCELLED → Customer cancelled manually. If amount_received > 0, a refund
              may be needed.

WEBHOOK PAYLOAD FIELDS (new in v2):
  amount_received — total USDT confirmed on-chain for this invoice
  shortfall       — how much more is needed (0.000000 when PAID/OVERPAID)
  overpaid_by     — how much extra was sent (0.000000 when PAID/PARTIAL)

RULE: Only fulfil orders when paid=true (status PAID or OVERPAID).
      Never fulfil on PARTIAL — the payment is incomplete.

Typical flow:
1. create_payment → get payment_url
2. Share payment_url with the customer — openbcp hosts the checkout page
3. Customer scans QR or copies address, sends USDT on BSC
4. Webhook fires to callback_url on every status change
5. Fulfil order only when webhook paid=true

RESUME PAYMENT:
- If a customer paid partially and the invoice expired, use resume_payment
  instead of refunding. It creates a continuation invoice for the shortfall
  reusing the same deposit address. When paid, the original invoice is
  marked PAID via cascade and the webhook fires on the ORIGINAL invoice_id.
- Idempotent: calling twice returns the existing continuation.

MULTI-WALLET PAYMENTS:
- A single invoice can receive payments from unlimited wallets.
- Each transaction's from_address is tracked separately.
- For underpaid + expired: each sender gets their own refund record.
- For overpaid: FIFO routing — the sender who tipped over 100% gets their
  excess portion back. Merchant initiates via Dashboard → Transactions.
""",
)


def _client() -> PayGateClient:
    url = os.environ.get("PAYGATE_URL", "").rstrip("/")
    key = os.environ.get("PAYGATE_API_KEY", "")
    if not url:
        raise RuntimeError(
            "PAYGATE_URL is not set. Add it to the MCP server env config."
        )
    if not key:
        raise RuntimeError(
            "PAYGATE_API_KEY is not set. Get your key from the PayGate dashboard → Integration."
        )
    return PayGateClient(base_url=url, api_key=key)


# ── Resource ──────────────────────────────────────────────────────────────────

@mcp.resource("paygate://config")
def get_config() -> str:
    """Shows current PayGate configuration status (for debugging)."""
    url = os.environ.get("PAYGATE_URL", "NOT SET")
    has_key = bool(os.environ.get("PAYGATE_API_KEY"))
    return (
        f"PayGate URL:        {url}\n"
        f"API Key configured: {'yes' if has_key else 'NO — set PAYGATE_API_KEY env var'}\n"
        f"Network:            BNB Smart Chain (BEP-20)\n"
        f"Token:              USDT\n"
    )


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def create_payment(
    amount_usdt: str,
    external_id: Optional[str] = None,
    callback_url: Optional[str] = None,
    description: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create a new USDT payment invoice.

    Args:
        amount_usdt:  Amount to charge in USDT as a string, e.g. "29.99".
                      Must be positive. Do not use a float.
        external_id:  Your order or reference ID — stored with the invoice
                      and included in webhook callbacks.
        callback_url: URL to receive a signed webhook POST when payment status
                      changes. Optional but recommended for production.
        description:  Human-readable label stored as invoice metadata.

    Returns:
        payment_url     — hosted checkout page URL — share this with the customer
        invoice_id      — store this, needed for all status checks
        amount_usdt     — gross amount customer pays (includes platform fee)
        merchant_amount_usdt — amount merchant receives after fee
        platform_fee_usdt    — openbcp platform fee added to the invoice
        expires_at      — ISO 8601 expiry (24 h from now by default)
        payment_status  — always "UNPAID" on creation
    """
    client = None
    try:
        client = _client()
        metadata = {"description": description} if description else None
        invoice = client.create_payment(
            amount_usdt,
            external_id=external_id,
            callback_url=callback_url,
            metadata=metadata,
        )
        return {
            "payment_url": invoice.payment_url,
            "invoice_id": invoice.invoice_id,
            "amount_usdt": str(invoice.amount_usdt),
            "merchant_amount_usdt": str(invoice.merchant_amount_usdt) if invoice.merchant_amount_usdt else None,
            "platform_fee_usdt": str(invoice.platform_fee_usdt) if invoice.platform_fee_usdt else None,
            "network": invoice.network,
            "token": invoice.token,
            "expires_at": invoice.expires_at.isoformat() if invoice.expires_at else None,
            "payment_status": invoice.payment_status,
        }
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        if client:
            client.close()


@mcp.tool()
def get_payment(invoice_id: int) -> dict[str, Any]:
    """
    Get full invoice details including all on-chain transactions.

    Use this for a complete picture — confirmations count, transaction IDs,
    total amount received, and current status.

    Args:
        invoice_id: Integer invoice ID returned by create_payment.

    Returns:
        Full invoice with payment_status, transactions list, external_id,
        created_at, and expires_at. payment_status is one of:
        UNPAID, PARTIAL, PAID, OVERPAID, EXPIRED.
    """
    client = None
    try:
        client = _client()
        invoice = client.get_payment(invoice_id)
        return {
            "invoice_id": invoice.invoice_id,
            "external_id": invoice.external_id,
            "deposit_address": invoice.deposit_address,
            "amount_usdt": str(invoice.amount_usdt),
            "payment_status": invoice.payment_status,
            "is_paid": invoice.is_paid(),
            "total_confirmed_usdt": str(invoice.total_confirmed),
            "network": invoice.network,
            "token": invoice.token,
            "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
            "expires_at": invoice.expires_at.isoformat() if invoice.expires_at else None,
            "transactions": [
                {
                    "txid": t.txid,
                    "amount_usdt": str(t.amount_usdt),
                    "confirmations": t.confirmations,
                    "required_confirmations": t.required_confirmations,
                    "status": t.status,
                }
                for t in invoice.transactions
            ],
        }
    except PaymentNotFound:
        return {"error": f"Invoice {invoice_id} not found", "status_code": 404}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        if client:
            client.close()


@mcp.tool()
def check_payment_status(invoice_id: int) -> dict[str, Any]:
    """
    Quick payment status check using the public endpoint (no API key needed).

    Lighter than get_payment — use this to poll whether a payment has landed.
    Returns is_paid, best confirmation count, and a human-readable summary.

    Args:
        invoice_id: Integer invoice ID.

    Returns:
        payment_status, is_paid, amount_usdt, best_confirmations,
        required_confirmations, and a plain-English summary string.
    """
    client = None
    try:
        client = _client()
        invoice = client.get_payment_status(invoice_id)

        confs, req = 0, 12
        if invoice.transactions:
            best = max(invoice.transactions, key=lambda t: t.confirmations)
            confs = best.confirmations
            req = best.required_confirmations

        received   = invoice.amount_received or invoice.total_confirmed
        shortfall  = invoice.shortfall  or max(Decimal("0"), invoice.amount_usdt - received)
        overpaid_by = invoice.overpaid_by or max(Decimal("0"), received - invoice.amount_usdt)

        if invoice.is_paid():
            if invoice.payment_status == "OVERPAID":
                summary = (f"Payment confirmed (overpaid by {overpaid_by} USDT). "
                           f"Received {received} USDT, expected {invoice.amount_usdt} USDT. "
                           "Fulfil the order and consider refunding the excess.")
            else:
                summary = f"Payment confirmed. Received {received} USDT. Fulfil the order."
        elif invoice.is_partial():
            summary = (f"Partial payment received: {received} USDT of "
                       f"{invoice.amount_usdt} USDT required. "
                       f"Still waiting for {shortfall} USDT more. "
                       "Do NOT fulfil the order yet.")
        elif invoice.is_expired():
            if received > Decimal("0"):
                summary = (f"Invoice expired. Customer sent {received} USDT but "
                           f"the payment window closed. A refund of {received} USDT may be needed.")
            else:
                summary = "Invoice expired — no payment received."
        elif invoice.is_cancelled():
            if received > Decimal("0"):
                summary = f"Order cancelled. {received} USDT was received — a refund may be needed."
            else:
                summary = "Order cancelled — no payment received."
        elif confs > 0:
            summary = f"Payment detected — waiting for confirmations ({confs}/{req})."
        else:
            summary = "Waiting for payment. No transaction detected yet."

        return {
            "invoice_id": invoice.invoice_id,
            "payment_status": invoice.payment_status,
            "is_paid": invoice.is_paid(),
            "amount_usdt": str(invoice.amount_usdt),
            "amount_received": str(received),
            "shortfall": str(shortfall),
            "overpaid_by": str(overpaid_by),
            "expires_at": invoice.expires_at.isoformat() if invoice.expires_at else None,
            "transactions_count": len(invoice.transactions),
            "best_confirmations": confs,
            "required_confirmations": req,
            "summary": summary,
        }
    except PaymentNotFound:
        return {"error": f"Invoice {invoice_id} not found", "status_code": 404}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        if client:
            client.close()


@mcp.tool()
def resume_payment(invoice_id: int) -> dict[str, Any]:
    """
    Resume an EXPIRED invoice with partial payment — creates a continuation
    invoice for the shortfall amount only. Reuses the SAME deposit address
    so the customer can pay via the old QR/URL OR the new payment_url.

    WHEN TO USE: Customer paid partially, invoice expired, customer wants to
    complete the payment (NOT refund). This avoids a refund/repurchase cycle.

    Cascade behaviour:
    - When the customer pays the continuation, the original invoice is
      automatically marked PAID.
    - Webhooks fire on the ORIGINAL invoice_id — merchant code doesn't change.
    - Any pending refund records for the original invoice are cancelled.

    Restrictions:
    - Only works on invoices with status EXPIRED and amount_received > 0.
    - Strictly per-tenant: can only resume your own invoices.

    Args:
        invoice_id: ID of the original EXPIRED invoice.

    Returns:
        continuation_invoice_id  — the new continuation invoice
        original_invoice_id      — the original invoice (cascade target)
        amount_usdt              — the shortfall (only what's still needed)
        amount_already_received  — what the customer already paid
        deposit_address          — same as the original invoice
        payment_url              — share this with the customer
        expires_at               — new expiry for the continuation
        cancelled_refund_count   — refunds cancelled due to resume

        If already resumed, returns resumed_existing: true (idempotent).
    """
    client = None
    try:
        client = _client()
        result = client.resume_payment(invoice_id)
        return result
    except PaymentNotFound:
        return {"error": f"Invoice {invoice_id} not found", "status_code": 404}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        if client:
            client.close()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
