# paygate-mcp

MCP server for the [openbcp](https://openbcp.com) USDT BEP20 payment gateway.
Lets AI assistants (Claude, etc.) create invoices, check payment status, and resume partial payments directly.

> **No Docker, no hosted server.** Claude Desktop runs this as a local subprocess on your machine.

## Setup (Claude Desktop)

**1. Install** (one time):
```bash
uvx --from git+https://github.com/usdt-paygate/paygate-mcp.git paygate-mcp --help
```

**2. Add to Claude Desktop config:**

MacOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "paygate": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/usdt-paygate/paygate-mcp.git", "paygate-mcp"],
      "env": {
        "PAYGATE_URL": "https://openbcp.com",
        "PAYGATE_API_KEY": "your-api-key-from-dashboard"
      }
    }
  }
}
```

**3. Restart Claude Desktop** — you'll see a 🔌 PayGate tool indicator.

## Tools

| Tool | What it does |
|------|-------------|
| `create_payment(amount_usdt, external_id?, callback_url?, description?)` | Create an invoice → returns `deposit_address` + `invoice_id` |
| `get_payment(invoice_id)` | Full invoice details with all on-chain transactions and `is_paid` |
| `check_payment_status(invoice_id)` | Quick status check with `amount_received`, `shortfall`, `overpaid_by`, and a plain-English summary |
| `resume_payment(invoice_id)` | Resume an EXPIRED-with-partial invoice — creates a continuation invoice for the shortfall, reusing the same deposit address |
| `list_refunds(status?, type?, invoice_id?, page?, limit?)` | List refunds with filters (for platform/SaaS merchants) |
| `approve_refund(refund_id)` | Approve a pending refund — queues for the midnight batch |
| `decline_refund(refund_id, reason?)` | Decline a pending refund — moves to blacklist |
| `get_refund_settings()` | Read per-type refund mode (underpaid + overpaid) |
| `update_refund_settings(underpaid_mode?, overpaid_mode?)` | Update per-type refund mode — each accepts 'auto' or 'manual' |

## What the AI assistant understands

The server tells Claude about openbcp's payment lifecycle and rules, so it knows:

- **PAID / OVERPAID** → safe to fulfil
- **PARTIAL** → customer underpaid, do NOT fulfil; check the `shortfall` field
- **EXPIRED with `amount_received > 0`** → funds received, suggest `resume_payment` before refunding
- **CANCELLED** → cancel order
- **Multi-wallet payments** → unlimited senders per invoice; FIFO routing for overpayment refunds

## Example prompts

> *"Create a $50 USDT payment for order #ABC-123"*
> *"Check if invoice 42 has been paid"*
> *"Invoice 42 expired with partial payment — resume it for the customer"*
> *"What's the status of invoice 99? Use the summary to tell me if I should ship the order"*

## Resource

- `paygate://config` — shows your configured URL and whether the API key is set (useful for debugging)
