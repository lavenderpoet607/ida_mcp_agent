# IDA Pro & ReClass.NET MCP Agent

Bridge integrasi Model Context Protocol (MCP) untuk menghubungkan AI Coding & Reverse Engineering Assistant (Antigravity IDE, Claude Desktop, Cursor, Windsurf) langsung ke **IDA Pro** dan **ReClass.NET**.

---

## Fitur Utama

- **IDA Pro MCP Server & UI Dashboard (`rea_agent.py`)**
  - Endpoint HTTP JSON-RPC 2.0 di `http://127.0.0.1:13337/mcp`
  - Mendukung 48+ fungsi analisis (dekompilasi Hex-Rays, disassembly, cross-references, tipe data struct, renaming symbol, memory read/write)
  - Dilengkapi antarmuka grafis (Qt Dashboard) di dalam IDA Pro untuk memantau status agen dan telemetri RPC secara real-time
- **ReClass.NET MCP Server (`reclass_mcp_server.py`)**
  - Bridge TCP socket di port `127.0.0.1:27015`
  - Memungkinkan AI Agent membaca memori proses target, membedah struktur class/struct, dan memanipulasi node data secara dinamis
- **Auto-Find Installer (`install.ps1`)**
  - Otomatis mendeteksi proses IDA Pro yang sedang berjalan, registry, dan direktori instalasi tanpa hardcode path
  - Otomatis mendeteksi ReClass.NET dan memasang plugin DLL
  - Mengonfigurasi file konfigurasi Claude Desktop secara otomatis

---

## Instalasi Cepat via PowerShell

### 1. Instalasi Standar (Hanya IDA Pro MCP)

Jalankan perintah berikut di PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/lavenderpoet607/ida_mcp_agent/main/install.ps1 | iex"
```

Atau jika dijalankan secara lokal:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

### 2. Instalasi Lengkap (IDA Pro MCP + ReClass.NET MCP)

Gunakan flag `-InstallReClass` untuk menyertakan plugin dan server ReClass.NET:

```powershell
powershell -ExecutionPolicy Bypass -Command "& { $(irm https://raw.githubusercontent.com/lavenderpoet607/ida_mcp_agent/main/install.ps1) } -InstallReClass"
```

Atau secara lokal:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallReClass
```


---

## Prasyarat Sistem

| Komponen | Versi Minimal |
| :--- | :--- |
| **Sistem Operasi** | Windows 10 / 11 (64-bit) |
| **Python** | 3.11 atau yang lebih baru |
| **IDA Pro** | 7.x / 8.x / 9.x dengan Python 3 |
| **ReClass.NET** | x64 Edition |

---

## Konfigurasi AI Agent

### 1. Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json`)

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

### 2. Antigravity IDE / Cursor (`.cursor/mcp.json` atau `ida-mcp.json`)

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

---

## Struktur File Repositori

```text
ida_mcp_agent/
├── install.ps1               PowerShell Auto-Find Installer
├── requirements.txt          Daftar dependensi Python
├── rea_agent.py              Plugin IDA Pro (MCP Server + Qt Dashboard UI)
├── reclass_mcp_server.py     Server MCP Bridge untuk ReClass.NET
├── ida-mcp.json              Template konfigurasi MCP untuk Antigravity/Cursor
├── ida_agent_config.json     Konfigurasi model AI internal plugin IDA
├── ida_client.py             Modul client Python untuk berinteraksi dengan IDA MCP
├── pointer_resolver.py       Mesin analisis dan resolusi pointer otomatis
├── agent_runner.py           Script mandiri untuk menjalankan auto pointer resolver
├── DecrpytXOR.py             Script pembantu dekripsi string XOR
├── patch_and_comment_xor.py  Script pembantu patching dan anotasi string XOR
├── bin/
│   ├── ReClassMCP.dll        Plugin ReClass.NET compiled .NET
│   └── Newtonsoft.Json.dll   Dependensi JSON untuk ReClass.NET
└── .gitignore                File konfigurasi ignore Git

```

---

## Verifikasi Endpoint

Setelah IDA Pro dan ReClass.NET dijalankan:

1. **IDA Pro MCP Health Check**:
```powershell
python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:13337/mcp', data=b'{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"check_connection\",\"params\":[]}', timeout=3).read().decode())"
```

2. **ReClass.NET TCP Ping**:
```powershell
python -c "import socket; s = socket.socket(); s.connect(('127.0.0.1', 27015)); s.sendall(b'{\"command\":\"ping\",\"args\":{}}\n'); print(s.recv(1024).decode()); s.close()"
```
