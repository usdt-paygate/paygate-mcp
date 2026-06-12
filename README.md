# paygate-mcp

MCP server for the [PayGate](https://github.com/usdt-paygate/paygate-python) USDT BEP20 payment gateway.  
Lets AI assistants (Claude, etc.) create invoices and check payment status directly.

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
        "PAYGATE_URL": "http://your-paygate-url",
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
| `get_payment(invoice_id)` | Full invoice details with transactions |
| `check_payment_status(invoice_id)` | Quick status check with plain-English summary |

## Example prompts

> *"Create a $50 USDT payment for order #ABC-123"*  
> *"Check if invoice 42 has been paid"*  
> *"What's the status of my last payment?"*

## Resource

- `paygate://config` — shows your configured URL and whether the API key is set (useful for debugging)
