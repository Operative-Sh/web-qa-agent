[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/operative-sh-web-eval-agent-badge.png)](https://mseep.ai/app/operative-sh-web-eval-agent)

# 🚀 operative.sh web-eval-agent MCP Server

> *Let the coding agent debug itself, you've got better things to do.*

![Demo](./demo.gif)



## 🔥 Supercharge Your Debugging

[operative.sh](https://www.operative.sh/mcp)'s MCP Server launches a browser-use powered agent to autonomously execute and debug web apps directly in your code editor.

## ⚡ Features

- 🌐 **Navigate your webapp** using BrowserUse (2x faster with operative backend)
- 📊 **Capture network traffic** - requests are intelligently filtered and returned into the context window
- 🚨 **Collect console errors** - captures logs & errors
- 🤖 **Autonomous debugging** - the Cursor agent calls the web QA agent mcp server to test if the code it wrote works as epected end-to-end.

## 🧰 MCP Tool Reference

| Tool | Purpose |
|------|---------|
| `web_eval_agent` | 🤖 Automated UX evaluator that drives the browser, captures screenshots, console & network logs, and returns a rich UX report. |
| `setup_browser_state` | 🔒 Opens an interactive (non-headless) browser so you can sign in once; the saved cookies/local-storage are reused by subsequent `web_eval_agent` runs. |

**Key arguments**

* `web_eval_agent`
  * `url` **(required)** – address of the running app (e.g. `http://localhost:3000`)
  * `task` **(required)** – natural-language description of what to test ("run through the signup flow and note any UX issues")
  * `headless_browser` *(optional, default `false`)* – set to `true` to hide the browser window

* `setup_browser_state`
  * `url` *(optional)* – page to open first (handy to land directly on a login screen)

You can trigger these tools straight from your IDE chat, for example:

```bash
Evaluate my app at http://localhost:3000 – run web_eval_agent with the task "Try the full signup flow and report UX issues".
```

## 🏁 Quick Start (macOS/Linux)

1. Pre-requisites (typically not needed):
 - brew: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
 - npm: (`brew install npm`)
 - jq: `brew install jq` 
2. Run the installer after [getting an api key (free)](https://www.operative.sh/mcp)
   - Installs [playwright](https://github.com/microsoft/playwright) 
   - [Installs uv](https://astral.sh/)
   - Inserts JSON into your code editor (Cursor/Cline/Windsurf) for you! 
```bash
curl -LSf https://operative.sh/install.sh -o install.sh && bash install.sh && rm install.sh
```
3. Visit your favorite IDE and restart to apply the changes
4. Send a prompt in chat mode to call the web eval agent tool! e.g. 
```bash
Test my app on http://localhost:3000. Use web-eval-agent.
```

## 🛠️ Manual Installation
1. Get your API key at operative.sh/mcp
2. [Install uv](https://docs.astral.sh/uv/#highlights)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

3. Source environment variables after installing UV

Mac
```
source ~/.zshrc
```

Linux 
```
source ~/.bashrc 
```
4. Install playwright:

```bash
npm install -g chromium playwright && uvx --with playwright playwright install --with-deps
```
5. Add below JSON to your relevant code editor with api key 
6. Restart your code editor
   
## 🔃 Updating 
- `uv cache clean`
- refresh MCP server 

```json 
    "web-eval-agent": {
      "command": "uvx",
      "args": [
        "--refresh-package",
        "webEvalAgent",
        "--from",
        "git+https://github.com/Operative-Sh/web-eval-agent.git",
        "webEvalAgent"
      ],
      "env": {
        "OPERATIVE_API_KEY": "<YOUR_KEY>"
      }
    }
```
## [Operative Discord Server](https://discord.gg/ryjCnf9myb)

## 🛠️ Manual Installation (Mac + Cursor/Cline/Windsurf) 
1. Get your API key at operative.sh/mcp
2. [Install uv](https://docs.astral.sh/uv/#highlights)
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh)
```
3. Install playwright:
```bash
npm install -g chromium playwright && uvx --with playwright playwright install --with-deps
```
4. Add below JSON to your relevant code editor with api key 
5. Restart your code editor

## Manual Installation (Windows + Cursor/Cline/Windsurf)  

We're refining this, please open an issue if you have any issues! 
1. Do all this in your code editor terminal 
2. `curl -LSf https://operative.sh/install.sh -o install.sh && bash install.sh && rm install.sh`
3. Get your API key at operative.sh/mcp
4. Install uv `(curl -LsSf https://astral.sh/uv/install.sh | sh)`
5. `uvx --from git+https://github.com/Operative-Sh/web-eval-agent.git playwright install`
6. Restart code editor 


## 🚨 Issues 
- Updates aren't being received in code editors, update or reinstall for latest version: Run `uv cache clean` for latest 
- Any issues feel free to open an Issue on this repo or in the discord!
- 5/5 - static apps without changes weren't screencasting, fixed! `uv clean` + restart to get fix

## Changelog 
- 4/29 - Agent overlay update - pause/play/stop agent run in the browser

## 📋 Example MCP Server Output Report

```text
📊 Web Evaluation Report for http://localhost:5173 complete!
📝 Task: Test the API-key deletion flow by navigating to the API Keys section, deleting a key, and judging the UX.

🔍 Agent Steps
  📍 1. Navigate → http://localhost:5173
  📍 2. Click     "Login"        (button index 2)
  📍 3. Click     "API Keys"     (button index 4)
  📍 4. Click     "Create Key"   (button index 9)
  📍 5. Type      "Test API Key" (input index 2)
  📍 6. Click     "Done"         (button index 3)
  📍 7. Click     "Delete"       (button index 10)
  📍 8. Click     "Delete"       (confirm index 3)
🏁 Flow tested successfully – UX felt smooth and intuitive.

🖥️ Console Logs (10)
  1. [debug] [vite] connecting…
  2. [debug] [vite] connected.
  3. [info]  Download the React DevTools …
     …

🌐 Network Requests (10)
  1. GET /src/pages/SleepingMasks.tsx                   304
  2. GET /src/pages/MCPRegistryRegistry.tsx             304
     …

⏱️ Chronological Timeline
  01:16:23.293 🖥️ Console [debug] [vite] connecting…
  01:16:23.303 🖥️ Console [debug] [vite] connected.
  01:16:23.312 ➡️ GET /src/pages/SleepingMasks.tsx
  01:16:23.318 ⬅️ 304 /src/pages/SleepingMasks.tsx
     …
  01:17:45.038 🤖 🏁 Flow finished – deletion verified
  01:17:47.038 🤖 📋 Conclusion repeated above
👁️  See the "Operative Control Center" dashboard for live logs.
```

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Operative-Sh/web-eval-agent&type=Date)](https://www.star-history.com/#Operative-Sh/web-eval-agent&Date)


---

Built with <3 @ [operative.sh](https://www.operative.sh)
