import sys
import json
from ida_client import IdaClient
from pointer_resolver import PointerResolver

def run_agent(target_address="0x1d88ec"):
    client = IdaClient()
    meta = client.get_metadata()
    if not meta:
        print("[-] Gagal terhubung ke IDA Pro MCP Server di port 13337")
        return

    print("==================================================")
    print("  IDA PRO MCP AGENT - AUTOMATED POINTER RESOLVER  ")
    print("==================================================")
    print(f"[*] Module: {meta.get('module')} ({meta.get('path')})")
    print(f"[*] Base Address: {meta.get('base')}")
    print(f"[*] Target Fungsi: {target_address}")

    resolver = PointerResolver(client)
    res = resolver.analyze_and_resolve(target_address, auto_annotate=True)

    if "error" in res:
        print(f"[-] Error: {res['error']}")
        return

    print(f"\n[+] Nama Fungsi: {res['function_name']}")
    print(f"[+] Total Baris Disassembly: {res['total_lines']}")
    print(f"[+] Total Pointer Teridentifikasi: {res['resolved_pointer_count']}")

    print("\n[+] Call Pointers (Subrutin yang dipanggil):")
    for cp in res["call_pointers"]:
        print(f"    - Alamat: {cp['source_address']} | Target: {cp['target_symbol']} ({cp['target_pointer']}) | Instruksi: {cp['instruction']}")

    print(f"\n[+] Branch Pointers: {len(res['branch_pointers'])} percabangan terpetakan")
    for bp in res["branch_pointers"][:8]:
        print(f"    - Alamat: {bp['source_address']} -> {bp['target_symbol']} ({bp['target_pointer']})")
    if len(res["branch_pointers"]) > 8:
        print(f"    ... [{len(res['branch_pointers']) - 8} branch pointers lainnya]")

    if res["data_pointers"]:
        print("\n[+] Data / Global Pointers:")
        for dp in res["data_pointers"]:
            print(f"    - Alamat: {dp['source_address']} -> {dp['target_symbol']} | {dp['instruction']}")

    print("\n[+] Caller Pointers (Xrefs ke fungsi ini):")
    for xp in res["caller_pointers"]:
        fn = xp.get("function")
        fname = fn.get("name") if fn else "Tabel Data/Export"
        print(f"    - Pemanggil: {xp['caller_address']} ({fname}) [Tipe: {xp['type']}]")

    print("\n[+] Dekompilasi Pseudocode Hex-Rays:")
    code = client.decompile(target_address)
    if code:
        for idx, line in enumerate(code.splitlines()[:20]):
            print(f"    {line}")

    print("\n[+] Status: Seluruh pointer dan komentar berhasil ditambahkan ke database IDA Pro secara otomatis!")

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "0x1d88ec"
    run_agent(target)
