# Connect a ChatGPT Dot

Dots are persistent ChatGPT agents. They have no public API you can push messages into, so the Eye works the other way around: it exposes an MCP server, you add it to ChatGPT as an app, and your Dot checks it on a schedule and messages you.

Pushcut stays the fast path for alerts. The Dot adds context and conversation ("you were away for 25 minutes of your focus block, want me to move the rest to tonight?").

> **Status:** the MCP server, the token check and every tool are tested. Adding it to ChatGPT and running it from a Dot has not been tested end to end yet, and ChatGPT's connector settings change often. If a step below no longer matches what you see, the server side still works the same way.

## 1. Serve MCP over HTTP

Make a long random token and put it in `secrets.env`:

```bash
python -c "import secrets; print('BBEYE_MCP_TOKEN=' + secrets.token_urlsafe(32))"
```

In `config.toml`, let `bbeye run` serve MCP next to the camera loop:

```toml
[mcp]
serve_http = true
port = 8765
```

Restart the Eye (or `bbeye service install` again). It now answers on `http://127.0.0.1:8765/mcp`, and every request must carry the token.

## 2. Give it an HTTPS address

ChatGPT runs in the cloud, so it needs a public HTTPS URL. With [Tailscale Funnel](https://tailscale.com/kb/1223/funnel):

```bash
tailscale funnel --bg 8765
```

It prints an address like `https://my-laptop.tailnet-name.ts.net`. Add that host name to the config so the server accepts requests for it:

```toml
[mcp]
allowed_hosts = ["my-laptop.tailnet-name.ts.net"]
```

Funnel makes the endpoint reachable from the internet. The token is what keeps it private, so do not share the URL with the token in it.

## 3. Add it to ChatGPT

1. In ChatGPT settings, open Apps (or Connectors), then Advanced, and turn on developer mode.
2. Create a new app or connector.
3. URL: `https://my-laptop.tailnet-name.ts.net/mcp?token=YOUR_TOKEN`
4. Authentication: none. ChatGPT's form offers OAuth or no authentication, so the token rides in the URL. If your version lets you set an `Authorization` header, use `Bearer YOUR_TOKEN` there instead and drop it from the URL.
5. Save, then ask ChatGPT "What does Big Brother Eye see right now?" to check it can call `get_status`.

## 4. Give your Dot a routine

Paste this into your Dot as a scheduled task:

```text
Every 15 minutes, use the Big Brother Eye app:
1. Call list_alerts. For each alert, send me its title and body in one short message,
   then call acknowledge_alert with its id.
2. If get_status says eye_online is false between 9:00 and 18:00 on a weekday, tell me once
   that the camera is off, and do not repeat it until it comes back.
Do not message me when there is nothing new.
```

Things you can ask it any time:

* "How many hours did I work today?" (`get_work_hours`)
* "Was anyone at my desk while I was out this afternoon?" (`get_timeline`)
* "Am I at my desk right now?" (`get_status`)
