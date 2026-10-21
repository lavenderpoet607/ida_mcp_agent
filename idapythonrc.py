import sys
import os
import ida_loader
import ida_kernwin

try:
    ida_loader.load_and_run_plugin("rea_agent", 0)
except Exception as e:
    try:
        ida_kernwin.msg(f"[idapythonrc] Notice: {e}\n")
    except Exception:
        pass
