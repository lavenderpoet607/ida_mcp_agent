# IDA Pro & ReClass.NET MCP Agent

A Model Context Protocol (MCP) integration bridge connecting modern AI coding and reverse engineering assistants (Google DeepMind Antigravity, Anthropic Claude Desktop / Claude Code, Cursor, Windsurf) directly to **IDA Pro** and **ReClass.NET**.

---

## Key Features

- **IDA Pro MCP Server & UI Dashboard (`rea_agent.py`)**
  - High-performance HTTP JSON-RPC 2.0 server at `http://127.0.0.1:13337/mcp`
  - 48+ reverse engineering analysis tools (Hex-Rays decompilation, disassembly, cross-references, struct definition analysis, symbol renaming, memory read/write)
  - Integrated Qt Dashboard inside IDA Pro displaying real-time agent status, RPC stream telemetry, and decompiled code view
  - **Dynamic Workspace & Interactive File Picker**: No hardcoded file paths. The active workspace directory is automatically detected from the binary opened in IDA Pro (`idaapi.get_input_file_path()`), or selected manually using the **"Browse File..."** button (`QFileDialog`) to choose an MCP configuration file (`ida-mcp.json`) or runner script (`agent_runner.py`)
  - **Custom C Export Path**: The **"Save C File..."** button utilizes `QFileDialog.getSaveFileName`, allowing users to save decompiled pseudocode anywhere with custom naming
  - **Pure Bilingual Support**: Seamlessly toggle between **English** and **Indonesian** from the header dropdown without mixed phrasing
  - **Auto-Load on Startup**: Automatically loaded upon launching IDA Pro via `idapythonrc.py` or directly from the `plugins/` directory

- **ReClass.NET MCP Server (`reclass_mcp_server.py`)**
  - TCP socket bridge on port `127.0.0.1:27015`
  - Enables AI agents to read process memory, inspect class and struct memory layouts, and manipulate data nodes dynamically

- **Auto-Find Installer (`install.ps1`)**
  - Automatically identifies running IDA Pro instances, Windows registry entries, and installation directories without hardcoded paths
  - Automatically locates ReClass.NET and installs the plugin DLL (optional with the `-InstallReClass` flag)
  - Automatically configures Claude Desktop MCP settings

---

## Quick Installation via PowerShell

### 1. Standard Installation (IDA Pro MCP Only)

Run the following command in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/lavenderpoet607/ida_mcp_agent/main/install.ps1 | iex"
```

Or run locally from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

### 2. Full Installation (IDA Pro MCP + ReClass.NET MCP)

Pass the `-InstallReClass` flag to include the ReClass.NET plugin and bridge server:

```powershell
powershell -ExecutionPolicy Bypass -Command "& { $(irm https://raw.githubusercontent.com/lavenderpoet607/ida_mcp_agent/main/install.ps1) } -InstallReClass"
```

Or run locally:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallReClass
```

---

## System Requirements

| Component | Minimum Version |
| :--- | :--- |
| **Operating System** | Windows 10 / 11 (64-bit) |
| **Python** | 3.11 or newer |
| **IDA Pro** | 7.x / 8.x / 9.x with Python 3 support |
| **ReClass.NET** | x64 Edition (optional) |

---

## AI Agent Configuration

### 1. Antigravity IDE / Cursor (`ida-mcp.json` or `.cursor/mcp.json`)

Select `ida-mcp.json` in the IDA Pro dashboard via the **"Browse File..."** button, or add the following block to your workspace root:

```json
{
  "command": "python",
  "args": [
    "-m",
    "ida_pro_mcp.server",
    "--ida-rpc",
    "http://127.0.0.1:13337"
  ]
}
```

### 2. Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "ida-pro": {
      "command": "python",
      "args": ["-m", "ida_pro_mcp.server", "--ida-rpc", "http://127.0.0.1:13337"]
    },
    "reclass": {
      "command": "python",
      "args": ["reclass_mcp_server.py"]
    }
  }
}
```

---

## Repository Structure

```text
ida_mcp_agent/
├── install.ps1               PowerShell Auto-Find Installer
├── requirements.txt          Python dependencies list
├── rea_agent.py              IDA Pro Plugin (MCP Server + Qt Dashboard UI + Interactive File Picker)
├── reclass_mcp_server.py     TCP Bridge MCP Server for ReClass.NET
├── idapythonrc.py            Startup script for auto-loading into IDA Pro
├── ida-mcp.json              MCP configuration template for Antigravity / Cursor
├── ida_agent_config.json     Internal AI model configuration and workspace preferences
├── ida_client.py             Python client module for interacting with IDA MCP
├── pointer_resolver.py       Automated pointer resolution and xref analysis engine
├── agent_runner.py           Standalone autonomous runner script
├── patch_and_comment_xor.py  Helper script for XOR patching and database annotation
├── bin/
│   ├── ReClassMCP.dll        Compiled ReClass.NET plugin DLL (.NET)
│   └── Newtonsoft.Json.dll   JSON dependency for ReClass.NET
├── .gitignore                Git ignore rules (caches, backups, and runtime artifacts)
└── README.md                 Official project documentation (English)
```

---

## Endpoint Verification

Once IDA Pro is running with the plugin loaded:

1. **IDA Pro MCP Health Check**:
```powershell
python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:13337/mcp', data=b'{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"check_connection\",\"params\":[]}', timeout=3).read().decode())"
```

2. **ReClass.NET TCP Ping** (when using ReClass.NET):
```powershell
python -c "import socket; s = socket.socket(); s.connect(('127.0.0.1', 27015)); s.sendall(b'{\"command\":\"ping\",\"args\":{}}\n'); print(s.recv(1024).decode()); s.close()"
```
