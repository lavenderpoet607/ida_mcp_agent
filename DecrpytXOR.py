import ida_bytes
import ida_kernwin
import idaapi
import idautils
import ida_funcs
import os
from collections import defaultdict

string_array_for_decryption_rva = 0x109B30
partial_key_rva = 0x5AA47C

global_results = []
global_viewer_title = "Decryption Results"
global_base = idaapi.get_imagebase()

def get_byte_at_rva(base, rva):
    actual_address = base + rva
    byte_data = ida_bytes.get_bytes(actual_address, 1)
    if byte_data is None:
        raise ValueError(f"Failed to read byte at RVA: {hex(rva)} (address {hex(actual_address)})")
    return byte_data[0]

def decrypt_string(base_address, main_key, xor_key, increment_size):
    DSTable = get_byte_at_rva(base_address, string_array_for_decryption_rva)
    partial_key = get_byte_at_rva(base_address, partial_key_rva)
    v5 = get_byte_at_rva(base_address, partial_key_rva + main_key + 1)
    pointer_to_complete_key = get_byte_at_rva(base_address, partial_key_rva + main_key)
    result_storage = []
    iterator = 0
    while iterator != 2:
        v12 = iterator == 1 or (pointer_to_complete_key << 0x18 == 0)
        if v12:
            main_key_plus1_byte = get_byte_at_rva(base_address, string_array_for_decryption_rva + main_key + 1)
            main_key_byte = get_byte_at_rva(base_address, string_array_for_decryption_rva + main_key)
            v5 = main_key_plus1_byte ^ main_key_byte
            v15 = v5
            if main_key_plus1_byte != main_key_byte:
                v16 = 0
                while v15 != v16:
                    main_key_v16_byte = get_byte_at_rva(base_address, string_array_for_decryption_rva + main_key + 2 + v16)
                    v17 = main_key_v16_byte ^ main_key_byte
                    v18 = (main_key_byte + v16) ^ xor_key
                    result_storage.append(v17 & 0xFF)
                    main_key_byte = (v18 + increment_size) & 0xFF
                    v16 += 1
            pointer_to_complete_key = 1
        v20 = 0xFF
        if v5 != 0:
            for byte in result_storage[:v5]:
                v20 ^= byte
        final_byte = get_byte_at_rva(base_address, string_array_for_decryption_rva + main_key + 2 + v5)
        if (final_byte ^ get_byte_at_rva(base_address, string_array_for_decryption_rva + main_key)) == (~v20 & 0xFF):
            break
        iterator += 1
    return bytes(result_storage[:v5])

def auto_guess_decryption(base_address, main_key, inc_min=1, inc_max=4, xor_start=0x00, xor_end=0xFF):
    candidates = []
    for xor_key in range(xor_start, xor_end + 1):
        for inc in range(inc_min, inc_max + 1):
            try:
                b = decrypt_string(base_address, main_key, xor_key, inc)
                if not b:
                    continue
                printable_count = sum(1 for x in b if 32 <= x < 127)
                if printable_count == 0:
                    continue
                try:
                    s = b.decode("utf-8")
                except Exception:
                    s = b.decode("latin-1", errors="ignore")
                if len(s) == 0:
                    continue
                if sum(1 for ch in s if 32 <= ord(ch) < 127) >= max(1, len(s)//2):
                    candidates.append((xor_key, inc, s))
            except Exception:
                continue
    return candidates

class DecryptViewer(idaapi.simplecustviewer_t):
    def Create(self, title, text_lines):
        if not idaapi.simplecustviewer_t.Create(self, title):
            return False
        for line in text_lines:
            self.AddLine(line)
        self.Show()
        return True
    def OnPopupMenu(self, menu_handle):
        idaapi.attach_action_to_popup(self, menu_handle, None, "decrypt:save_results")

def export_results_to_file(results, filename):
    try:
        folder = os.path.dirname(filename)
        if folder and not os.path.exists(folder):
            os.makedirs(folder)
        with open(filename, "w", encoding="utf-8") as f:
            f.write("MAIN_KEY,XOR_KEY,INC,STRING,CALLERS\n")
            for r in results:
                if len(r) == 5:
                    main_key, xor_key, inc, s, callers = r
                else:
                    main_key = ""
                    xor_key, inc, s, callers = r
                s_escaped = s.replace("\n", "\\n")
                callers_escaped = ";".join(callers) if callers else ""
                f.write(f"{main_key},{xor_key},{inc},\"{s_escaped}\",\"{callers_escaped}\"\n")
        ida_kernwin.msg("Results exported to: %s\n" % filename)
    except Exception as e:
        ida_kernwin.warning("Failed to export results: %s\n" % str(e))

class SaveResultsHandler(idaapi.action_handler_t):
    def __init__(self):
        idaapi.action_handler_t.__init__(self)
    def activate(self, ctx):
        global global_results
        if not global_results:
            ida_kernwin.msg("No results to save.\n")
            return 1
        filename = ida_kernwin.ask_file(False, "*.csv", "Save decrypted results as")
        if not filename:
            return 1
        export_results_to_file(global_results, filename)
        return 1
    def update(self, ctx):
        return idaapi.AST_ENABLE_ALWAYS

def register_actions():
    try:
        idaapi.unregister_action("decrypt:save_results")
    except Exception:
        pass
    act = idaapi.action_desc_t("decrypt:save_results","Save decrypted results",SaveResultsHandler(),None,"Save decrypted results to file",0)
    idaapi.register_action(act)

def get_callers_for_decrypted_string(base, main_key, length):
    callers = set()
    start = base + string_array_for_decryption_rva + main_key + 2
    end = start + max(0, length - 1)
    for addr in range(start, end + 1):
        for x in idautils.XrefsTo(addr):
            try:
                func = ida_funcs.get_func(x.frm)
                if func:
                    name = idaapi.get_func_name(func.start_ea)
                    callers.add(f"{name}@0x{func.start_ea:X}")
                else:
                    callers.add(f"ref@0x{x.frm:X}")
            except Exception:
                callers.add(f"ref@0x{x.frm:X}")
    return sorted(callers)

def show_grouped_results(title, results, include_main_key=False):
    lines = ["=== GROUPED DECRYPTION RESULTS ===", ""]
    grouped = defaultdict(list)
    if include_main_key:
        for main_key, xor_key, inc, s, callers in results:
            grouped[(main_key, xor_key)].append((inc, s, callers))
        by_main = defaultdict(list)
        for (m, x), entries in grouped.items():
            by_main[m].append((x, entries))
        for main_key in sorted(by_main.keys()):
            lines.append(f"=== MAIN_KEY = 0x{main_key:02X} ===")
            for xor_key, entries in sorted(by_main[main_key], key=lambda x: x[0]):
                lines.append(f"  -- XOR = 0x{xor_key:02X} --")
                for inc, s, callers in entries:
                    for l in s.splitlines() or [""]:
                        lines.append(f"    INC={inc} : {l}")
                    if callers:
                        lines.append(f"    Callers: {', '.join(callers)}")
                lines.append("")
            lines.append("")
    else:
        for main_key, xor_key, inc, s, callers in results:
            grouped[xor_key].append((main_key, inc, s, callers))
        for xor_key in sorted(grouped.keys()):
            lines.append(f"--- XOR Key = 0x{xor_key:02X} ---")
            for main_key, inc, s, callers in grouped[xor_key]:
                for l in s.splitlines() or [""]:
                    lines.append(f"  MAIN=0x{main_key:02X} INC={inc}: {l}")
                if callers:
                    lines.append(f"    Callers: {', '.join(callers)}")
            lines.append("")
    v = DecryptViewer()
    if not v.Create(title, lines):
        ida_kernwin.msg("Failed to create results viewer\n")

def run_single_key_flow(base_address):
    global global_results, global_viewer_title
    main_key = ida_kernwin.ask_long(0, "Enter the main key to test (0..255)")
    if main_key is None:
        ida_kernwin.msg("Cancelled.\n")
        return
    inc_min = ida_kernwin.ask_long(1, "Increment min value (default 1)")
    if inc_min is None:
        inc_min = 1
    inc_max = ida_kernwin.ask_long(4, "Increment max value (default 4)")
    if inc_max is None:
        inc_max = 4
    xor_start = ida_kernwin.ask_long(0, "XOR start (0..255) (default 0)")
    xor_end = ida_kernwin.ask_long(255, "XOR end (0..255) (default 255)")
    try:
        xor_start = int(xor_start) & 0xFF
        xor_end = int(xor_end) & 0xFF
    except Exception:
        xor_start, xor_end = 0, 255
    filter_keyword = ida_kernwin.ask_str("", 0, "Filter results containing (leave empty for all)")
    ida_kernwin.show_wait_box("Brute-forcing XOR keys (this may take a while)...")
    try:
        raw_results = auto_guess_decryption(base_address, main_key, inc_min=inc_min, inc_max=inc_max, xor_start=xor_start, xor_end=xor_end)
    finally:
        ida_kernwin.hide_wait_box()
    results_with_callers = []
    for xor_key, inc, s in raw_results:
        if filter_keyword and filter_keyword.lower() not in s.lower():
            continue
        callers = get_callers_for_decrypted_string(base_address, main_key, len(s))
        results_with_callers.append((main_key, xor_key, inc, s, callers))
    global_results = results_with_callers
    global_viewer_title = f"Decryption Results - main_key 0x{main_key:02X}"
    if global_results:
        show_grouped_results(global_viewer_title, global_results, include_main_key=True)
        ida_kernwin.msg("%d candidate(s) found. See '%s' tab.\n" % (len(global_results), global_viewer_title))
    else:
        ida_kernwin.info("No valid strings found for that key/range/filter.")

def run_auto_scan_flow(base_address):
    global global_results, global_viewer_title
    start_key = ida_kernwin.ask_long(0, "Start main_key (0..255)")
    end_key = ida_kernwin.ask_long(0xFF, "End main_key (0..255)")
    step = ida_kernwin.ask_long(1, "Step between main_keys (default 1)")
    if start_key is None or end_key is None or step is None:
        ida_kernwin.msg("Cancelled.\n")
        return
    try:
        start_key = int(start_key)
        end_key = int(end_key)
        step = int(step)
    except Exception:
        ida_kernwin.msg("Invalid numeric parameters.\n")
        return
    inc_min = ida_kernwin.ask_long(1, "Increment min value (default 1)")
    if inc_min is None: inc_min = 1
    inc_max = ida_kernwin.ask_long(4, "Increment max value (default 4)")
    if inc_max is None: inc_max = 4
    xor_start = ida_kernwin.ask_long(0, "XOR start (default 0)")
    xor_end   = ida_kernwin.ask_long(255, "XOR end (default 255)")
    try:
        xor_start = int(xor_start) & 0xFF
        xor_end   = int(xor_end) & 0xFF
    except Exception:
        xor_start, xor_end = 0, 255
    filter_keyword = ida_kernwin.ask_str("", 0, "Filter results containing (leave empty for all)")
    results_accum = []
    total_keys = ((end_key - start_key) // max(1, step)) + 1
    count = 0
    ida_kernwin.show_wait_box("Auto-scanning keys...")
    try:
        for main_key in range(start_key, end_key + 1, step):
            count += 1
            ida_kernwin.replace_wait_box(f"Testing main_key 0x{main_key:02X} ... ({count}/{total_keys})")
            try:
                res = auto_guess_decryption(base_address, main_key, inc_min=inc_min, inc_max=inc_max, xor_start=xor_start, xor_end=xor_end)
                for xor_key, inc, s in res:
                    if filter_keyword and filter_keyword.lower() not in s.lower():
                        continue
                    callers = get_callers_for_decrypted_string(base_address, main_key, len(s))
                    results_accum.append((main_key, xor_key, inc, s, callers))
            except Exception:
                continue
    finally:
        ida_kernwin.hide_wait_box()
    global_results = results_accum
    global_viewer_title = f"AutoScan Results 0x{start_key:02X}-0x{end_key:02X}"
    if global_results:
        show_grouped_results(global_viewer_title, global_results, include_main_key=True)
        ida_kernwin.msg("%d candidate(s) found across tested keys. See '%s' tab.\n" % (len(global_results), global_viewer_title))
    else:
        ida_kernwin.info("No valid strings found in auto-scan.")

def settings_menu_and_run(base_address):
    register_actions()
    choice = ida_kernwin.ask_long(1, "Choose mode: 1=Single main_key, 2=Auto-scan multiple keys, 3=sub_2E34C4 Reassemble Tab (enter 1, 2, or 3)")
    if choice is None:
        ida_kernwin.msg("No choice made, exiting.\n")
        return
    c = int(choice)
    if c == 1:
        run_single_key_flow(base_address)
    elif c == 2:
        run_auto_scan_flow(base_address)
    elif c == 3:
        try:
            import patch_and_comment_xor
            patch_and_comment_xor.show_results_in_tab()
        except ImportError:
            # If not in path, exec directly
            script_path = os.path.join(os.path.dirname(__file__), "patch_and_comment_xor.py")
            if os.path.exists(script_path):
                import importlib.util
                spec = importlib.util.spec_from_file_location("patch_and_comment_xor", script_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.show_results_in_tab()
            else:
                ida_kernwin.warning(f"Could not find patch_and_comment_xor.py at: {script_path}")

def main():
    base_address = global_base
    ida_kernwin.msg("Starting enhanced auto-decrypt script...\n")
    settings_menu_and_run(base_address)

if __name__ == "__main__":
    main()
