import re

class PointerResolver:
    def __init__(self, client):
        self.client = client

    def analyze_and_resolve(self, target_ea=None, auto_annotate=True):
        if not target_ea or target_ea == "auto":
            cur_fn = self.client.get_current_function()
            if cur_fn and isinstance(cur_fn, dict) and "start" in cur_fn:
                target_ea = hex(cur_fn["start"])
            else:
                cur_ea = self.client.get_current_address()
                target_ea = hex(cur_ea) if isinstance(cur_ea, int) else (str(cur_ea) if cur_ea else "0x0")
        func_info = self.client.get_function(target_ea)
        if not func_info:
            return {"error": "Fungsi tidak ditemukan", "target": target_ea}

        fname = func_info.get("name", "Unknown")
        disasm_data = self.client.disassemble(target_ea)
        if not disasm_data or "lines" not in disasm_data:
            return {"error": "Gagal disassemble fungsi", "target": target_ea}

        resolved_pointers = []
        call_pointers = []
        branch_pointers = []
        data_pointers = []

        for line in disasm_data["lines"]:
            addr = line.get("address", "")
            insn = line.get("instruction", "")

            bl_match = re.search(r"\bBL\s+([a-zA-Z0-9_]+)", insn)
            if bl_match:
                target_callee = bl_match.group(1)
                callee_info = self.client.get_function(target_callee)
                callee_addr = callee_info.get("address") if callee_info else target_callee
                entry = {
                    "type": "call",
                    "source_address": addr,
                    "target_symbol": target_callee,
                    "target_pointer": callee_addr,
                    "instruction": insn
                }
                call_pointers.append(entry)
                resolved_pointers.append(entry)
                if auto_annotate:
                    self.client.set_comment(addr, f"[AutoPointer] Call -> {target_callee} ({callee_addr})")

            branch_match = re.search(r"\bB(?:\.[A-Z]+)?\s+(loc_[0-9a-fA-F]+)", insn)
            if branch_match:
                loc_name = branch_match.group(1)
                loc_addr = "0x" + loc_name.split("_")[1].lower() if "_" in loc_name else loc_name
                entry = {
                    "type": "branch",
                    "source_address": addr,
                    "target_symbol": loc_name,
                    "target_pointer": loc_addr,
                    "instruction": insn
                }
                branch_pointers.append(entry)
                resolved_pointers.append(entry)

            data_match = re.search(r"\b(?:ADRP|ADRL|LDR|STR)\s+[^,]+,\s*([^,\s]+)", insn)
            if data_match:
                operand = data_match.group(1)
                if any(k in operand for k in ["qword_", "dword_", "off_", "unk_", "byte_"]):
                    entry = {
                        "type": "data_pointer",
                        "source_address": addr,
                        "target_symbol": operand,
                        "instruction": insn
                    }
                    data_pointers.append(entry)
                    resolved_pointers.append(entry)
                    if auto_annotate:
                        self.client.set_comment(addr, f"[AutoPointer] Ref -> {operand}")

        xrefs = self.client.get_xrefs(target_ea)
        caller_pointers = []
        if xrefs:
            for x in xrefs:
                caller_pointers.append({
                    "caller_address": x.get("address"),
                    "type": x.get("type"),
                    "function": x.get("function")
                })

        return {
            "function_name": fname,
            "function_address": target_ea,
            "total_lines": len(disasm_data["lines"]),
            "resolved_pointer_count": len(resolved_pointers),
            "call_pointers": call_pointers,
            "branch_pointers": branch_pointers,
            "data_pointers": data_pointers,
            "caller_pointers": caller_pointers
        }
