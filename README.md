# ScratchLink

ScratchLink is a desktop app for PenguinMod and Scratch-style projects that renders its interface with HTML, CSS, and JavaScript inside a Python-powered desktop window while routing its extension and API through a public Cloudflare URL.

The AI blocks use an online hosted model.

When the app starts, it blocks on setup until Cloudflare Tunnel is available and a public URL is live. After that, the app opens a connection dashboard where every ScratchLink connection has:

- its own name
- its own auto-generated password
- its own public extension link
- its own enabled or disabled state

Every action from the extension is verified against that connection's ID and password.

## What is in this repo

- `api_host.py`: the desktop app, local API host, HTML app launcher, and Cloudflare startup flow
- `scratchlink_penguinmod.js`: the PenguinMod extension template served by the app
- `requirements.txt`: Python dependencies for the app
- `ui/`: the HTML, CSS, and JavaScript files for the desktop interface

## Features

- startup gate that installs or downloads Cloudflare Tunnel before the app unlocks
- HTML-based desktop interface rendered through `pywebview`
- public Cloudflare URL used for both the extension and API
- connection cards shown in a responsive grid
- green highlight for enabled connections and red highlight for disabled ones
- pencil menu on each card for quick editing
- multiple named ScratchLink connections active at the same time
- per-connection password verification on every request
- turn any connection on or off without affecting the others
- copy-ready extension links for PenguinMod
- mouse, keyboard, screenshot, file, folder, app, and Roblox actions

## Requirements

- Python 3.11 or newer is recommended
- Windows is the main target right now
- internet access is needed when the app is downloading Cloudflare Tunnel or opening the public URL
- internet access is also needed for the AI blocks because they call a hosted AI model

Install dependencies with:

```bash
pip install -r requirements.txt
```

## Start the app

Run:

```bash
python api_host.py
```

You can also choose a custom local host or port for the background server:

```bash
python api_host.py --host 127.0.0.1 --port 8765
```

ScratchLink still runs a local background server, but the app now exposes it through Cloudflare and gives you a public URL for actual use.

## Hosted AI setup

ScratchLink's AI blocks call a hosted chat model through an OpenAI-compatible endpoint.

By default the app uses:

- endpoint: `https://router.huggingface.co/v1/chat/completions`
- model: `openai/gpt-oss-120b:fastest`

Before starting the app, set one of these environment variables:

- `SCRATCHLINK_AI_TOKEN`
- `HF_TOKEN`

Optional overrides:

- `SCRATCHLINK_AI_MODEL`
- `SCRATCHLINK_AI_ENDPOINT`

Example on Windows PowerShell:

```powershell
$env:SCRATCHLINK_AI_TOKEN="your-token-here"
python api_host.py
```

## Startup flow

On launch, ScratchLink does this before the main window becomes usable:

1. checks whether `cloudflared` is already installed
2. tries to install it automatically
3. falls back to downloading it directly if needed
4. starts the local ScratchLink server
5. opens a public Cloudflare URL
6. unlocks the main app window

Until that setup is done, the app stays on its startup screen and does not allow normal use.

## Using the desktop app

Once the app opens, it shows:

- a menu bar
- a grid of connection cards
- a details panel for the selected connection
- a server tab with the public API and docs URLs

Each connection card shows:

- connection name
- shortened connection ID
- enabled or disabled state
- last used time
- a pencil icon that opens the edit menu

From the app you can:

- create a new connection
- rename a connection
- turn a connection on or off
- regenerate its password
- copy its password
- copy its full extension link
- delete a connection

If you delete the last connection, the app automatically creates a fresh one so ScratchLink always stays usable.

## Connecting from PenguinMod

1. Start the ScratchLink desktop app.
2. Wait for the Cloudflare setup to finish.
3. Create or select a connection card.
4. Copy that connection's full extension link.
5. Load that link as a custom extension in PenguinMod.
6. Use the `ScratchLink` blocks in your project.

Each connection link looks like:

```text
/extension/<connection-id>.js?password=<generated-password>
```

That link is unique to one connection. Different projects can use different links at the same time.

## Authentication model

ScratchLink no longer uses one shared session for everything.

Instead, every request includes:

- a connection ID
- a password

The host verifies both on every action. If a connection is turned off, requests from that connection are rejected until it is turned back on.

## API and docs

The app publishes the API through its Cloudflare URL and also exposes docs at:

```text
<public-url>/docs
```

Main API areas include:

- health and connection checks
- hosted AI generation
- screen capture and monitor info
- mouse actions
- keyboard actions
- Roblox launching
- file and folder actions
- app launching
- buffered batch execution

## Notes

- connection data lives only in memory while the app is running
- `PyAutoGUI` fail-safe is disabled in the current host code
- regenerating a password invalidates the old extension link for that connection
- the local background server still exists, but the app is designed to share only the Cloudflare URL outward
- AI replies depend on your configured hosted provider and internet access

## Safety

ScratchLink can control your mouse, keyboard, files, and apps. Only use it in projects and environments you trust.
