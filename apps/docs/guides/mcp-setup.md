# MCP setup

This server speaks the **Model Context Protocol** so every endpoint you
register also surfaces as a callable tool inside Claude (Desktop, Code,
or any MCP-aware client). One endpoint definition → one REST route + one
MCP tool, no duplicate code.

## Claude.ai (web) — recommended

For the smoothest setup, use Claude.ai's custom-connector flow — it
handles OAuth (Dynamic Client Registration + PKCE) for you, no JSON
editing required.

1. **Settings → Connectors → Add custom server**.
2. Paste `{{ PUBLIC_BASE_URL }}/mcp/` as the server URL. Submit.
3. Sign in via magic-link, approve the consent screen, and your
   tools appear in the conversation.

If you'd rather skip the OAuth handshake (e.g. for a trusted personal
account), mint a **URL token** at
[/dashboard/url-tokens/](/dashboard/url-tokens/) → **+ New URL token**.
Copy the full `https://.../mcp/k/mcpurl_.../` URL it prints once and
paste that into the same Claude.ai connector field. No bearer header
needed — the token is part of the path.

## Claude Desktop

Claude Desktop only natively supports **stdio** MCP servers, so
connecting to a remote HTTP endpoint needs a small stdio shim. The
standard one is [`mcp-remote`](https://www.npmjs.com/package/mcp-remote).

Edit `claude_desktop_config.json` (location: `~/Library/Application
Support/Claude/` on macOS, `%APPDATA%\Claude\` on Windows):

```json
{
  "mcpServers": {
    "this-api": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "{{ PUBLIC_BASE_URL }}/mcp/",
        "--header",
        "Authorization: Bearer mcpsk_your_key_here"
      ]
    }
  }
}
```

Restart Claude Desktop. Open a new conversation; the available tools
appear automatically — every endpoint registered with
`@endpoint(slug=...)` surfaces as a tool named after its slug (e.g.
the `hello` endpoint shows up as `hello`).

## Claude Code

From your terminal:

```bash
claude mcp add this-api {{ PUBLIC_BASE_URL }}/mcp/ \
  --transport http \
  --header "Authorization: Bearer mcpsk_your_key_here"
```

This writes the server entry into `~/.claude.json` (user scope). Use
`claude mcp list` to verify, and `claude mcp remove this-api` to
detach. You can also point a session at an ad-hoc config with
`claude --mcp-config <file.json>`.

## Cursor

Cursor accepts remote MCP servers directly — no shim needed. Edit
`~/.cursor/mcp.json` (or the in-app settings):

```json
{
  "mcpServers": {
    "this-api": {
      "url": "{{ PUBLIC_BASE_URL }}/mcp/",
      "headers": {
        "Authorization": "Bearer mcpsk_your_key_here"
      }
    }
  }
}
```

Restart Cursor; tools appear under your configured server name.

## Authentication

Use the same `Authorization: Bearer mcpsk_<key>` token used for REST.
Each tool invocation counts against your credit quota — see the
[billing page](/dashboard/billing/) for your current balance.

If your client supports **OAuth-based MCP authentication** (instead of
a static bearer), point it at:

```
{{ PUBLIC_BASE_URL }}/oauth/authorize/
```

The Authorization Server metadata is published at
`/.well-known/oauth-authorization-server` and we support Dynamic
Client Registration (DCR) per RFC 7591 so most clients self-register
without operator action.

## Troubleshooting

  - **No tools show up**: open Claude Desktop devtools (Help →
    Developer Tools), check the MCP server log for connection errors.
    Most common cause: wrong `token`.
  - **Tools show but return errors**: check the request log on
    [/dashboard/usage/](/dashboard/usage/) for the failing call —
    status codes match the [error codes guide](/docs/guide/errors/).
  - **Tool name ends with `__v2` / `__v3`**: the endpoint ships multiple
    versions and the suffix marks the non-default one. The unsuffixed
    name is the stable v1; pick the suffixed version only if you
    specifically need its behavior.

## Local development

When developing endpoints locally, point your MCP client at
`http://localhost:8000/mcp/` (note: `http`, not `https`). Restart the
client after registering a new endpoint — Claude reads the tool list
once at connection time.
