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
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from paygate import PayGateClient, PayGateError, PaymentNotFound

mcp = FastMCP(
    "openbcp",
    instructions="""
You have access to an openbcp USDT BEP20 payment gateway via these tools.

openbcp lets merchants accept USDT on Binance Smart Chain (BEP-20).
Key facts:
- Payments need 12 on-chain confirmations (~60 seconds after the tx lands)
- Invoices expire after 24 hours by default
- Status values: UNPAID → PARTIAL → PAID / OVERPAID (or EXPIRED)
- PAID and OVERPAID both mean the merchant received sufficient funds
- Always pass amount_usdt as a string (e.g. "29.99"), never a float
- A platform fee is automatically added to the invoice amount

Typical flow:
1. create_payment → get payment_url
2. Share payment_url with the customer — openbcp hosts the checkout page
3. Customer scans QR or copies address, sends USDT on BSC
4. Webhook fires to callback_url when confirmed, or use check_payment_status
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

        if invoice.is_paid():
            summary = f"Payment confirmed. Received {invoice.total_confirmed} USDT."
        elif invoice.is_expired():
            summary = "Invoice expired — no payment received in time."
        elif confs > 0:
            summary = f"Payment detected — waiting for confirmations ({confs}/{req})."
        else:
            summary = "Waiting for payment. No transaction detected yet."

        return {
            "invoice_id": invoice.invoice_id,
            "payment_status": invoice.payment_status,
            "is_paid": invoice.is_paid(),
            "amount_usdt": str(invoice.amount_usdt),
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


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
