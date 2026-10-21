import os
import sys

if sys.version_info < (3, 11):
    raise RuntimeError("Python 3.11 or higher is required for the MCP plugin")

import json
import struct
import threading
import http.server
import base64
import urllib.request
import urllib.error
from urllib.parse import urlparse
from typing import (
    Any,
    Callable,
    get_type_hints,
    TypedDict,
    Optional,
    Annotated,
    TypeVar,
    Generic,
    NotRequired,
    overload,
    Literal,
)

class JSONRPCError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data

class RPCRegistry:
    def __init__(self):
        self.methods: dict[str, Callable] = {}
        self.unsafe: set[str] = set()

    def register(self, func: Callable) -> Callable:
        self.methods[func.__name__] = func
        return func

    def mark_unsafe(self, func: Callable) -> Callable:
        self.unsafe.add(func.__name__)
        return func

    def dispatch(self, method: str, params: Any) -> Any:
        if method not in self.methods:
            raise JSONRPCError(-32601, f"Method '{method}' not found")

        func = self.methods[method]
        hints = get_type_hints(func)

                                             
        hints.pop("return", None)

        if isinstance(params, list):
            if len(params) != len(hints):
                raise JSONRPCError(-32602, f"Invalid params: expected {len(hints)} arguments, got {len(params)}")

                                             
            converted_params = []
            for value, (param_name, expected_type) in zip(params, hints.items()):
                try:
                    if not isinstance(value, expected_type):
                        value = expected_type(value)
                    converted_params.append(value)
                except (ValueError, TypeError):
                    raise JSONRPCError(-32602, f"Invalid type for parameter '{param_name}': expected {expected_type.__name__}")

            return func(*converted_params)
        elif isinstance(params, dict):
            if set(params.keys()) != set(hints.keys()):
                raise JSONRPCError(-32602, f"Invalid params: expected {list(hints.keys())}")

                                             
            converted_params = {}
            for param_name, expected_type in hints.items():
                value = params.get(param_name)
                try:
                    if not isinstance(value, expected_type):
                        value = expected_type(value)
                    converted_params[param_name] = value
                except (ValueError, TypeError):
                    raise JSONRPCError(-32602, f"Invalid type for parameter '{param_name}': expected {expected_type.__name__}")

            return func(**converted_params)
        else:
            raise JSONRPCError(-32600, "Invalid Request: params must be array or object")

rpc_registry = RPCRegistry()

def jsonrpc(func: Callable) -> Callable:
    """Decorator to register a function as a JSON-RPC method"""
    global rpc_registry
    return rpc_registry.register(func)

def unsafe(func: Callable) -> Callable:
    """Decorator to register mark a function as unsafe"""
    return rpc_registry.mark_unsafe(func)

class JSONRPCRequestHandler(http.server.BaseHTTPRequestHandler):
    def send_jsonrpc_error(self, code: int, message: str, id: Any = None):
        response = {
            "jsonrpc": "2.0",
            "error": {
                "code": code,
                "message": message
            }
        }
        if id is not None:
            response["id"] = id
        response_body = json.dumps(response).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
            self.wfile.flush()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError, OSError):
            self.close_connection = True

    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path in ("/mcp", "/health", "/", "/ping"):
            response = {
                "status": "ok",
                "mcp": "running",
                "version": "2.0.0",
                "server": "IDA Pro MCP Server"
            }
            response_body = json.dumps(response).encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)
                self.wfile.flush()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError, OSError):
                self.close_connection = True
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        global rpc_registry

        parsed_path = urlparse(self.path)
        if parsed_path.path != "/mcp":
            self.send_jsonrpc_error(-32098, "Invalid endpoint", None)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_jsonrpc_error(-32700, "Parse error: missing request body", None)
            return

        request_body = self.rfile.read(content_length)
        try:
            request = json.loads(request_body)
        except json.JSONDecodeError:
            self.send_jsonrpc_error(-32700, "Parse error: invalid JSON", None)
            return

                              
        response: dict[str, Any] = {
            "jsonrpc": "2.0"
        }
        if request.get("id") is not None:
            response["id"] = request.get("id")

        try:
                                       
            if not isinstance(request, dict):
                raise JSONRPCError(-32600, "Invalid Request")
            if request.get("jsonrpc") != "2.0":
                raise JSONRPCError(-32600, "Invalid JSON-RPC version")
            if "method" not in request:
                raise JSONRPCError(-32600, "Method not specified")

            method = request["method"]
            params = request.get("params", [])
            if "McpDashboardState" in globals():
                McpDashboardState.record_rpc_call(method, params)
            elif "ReaBridgeState" in globals():
                ReaBridgeState.log_event("agent_call", f"Agent -> {method}()", params)

                                 
            result = rpc_registry.dispatch(method, params)
            response["result"] = result

            if "McpDashboardState" in globals():
                res_preview = ""
                if isinstance(result, list):
                    res_preview = f"({len(result)} items)"
                elif isinstance(result, dict):
                    res_preview = f"({len(result)} keys)"
                McpDashboardState.record_rpc_success(method, res_preview)
            elif "ReaBridgeState" in globals():
                res_preview = ""
                if isinstance(result, list):
                    res_preview = f"({len(result)} items)"
                elif isinstance(result, dict):
                    res_preview = f"({len(result)} keys)"
                ReaBridgeState.log_event("agent_success", f"Agent <- {method} OK {res_preview}")

        except JSONRPCError as e:
            response["error"] = {
                "code": e.code,
                "message": e.message
            }
            if e.data is not None:
                response["error"]["data"] = e.data
        except IDAError as e:
            response["error"] = {
                "code": -32000,
                "message": e.message,
            }
        except Exception as e:
            traceback.print_exc()
            response["error"] = {
                "code": -32603,
                "message": "Internal error (please report a bug)",
                "data": traceback.format_exc(),
            }

        try:
            response_body = json.dumps(response).encode("utf-8")
        except Exception as e:
            traceback.print_exc()
            response_body = json.dumps({
                "error": {
                    "code": -32603,
                    "message": "Internal error (please report a bug)",
                    "data": traceback.format_exc(),
                }
            }).encode("utf-8")

        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
            self.wfile.flush()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError, OSError):
            self.close_connection = True

    def log_message(self, format, *args):
                          
        pass

class MCPHTTPServer(http.server.HTTPServer):
    allow_reuse_address = True

    def handle_error(self, request, client_address):
                                                                                                                               
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError):
            return
        if isinstance(exc_val, OSError) and getattr(exc_val, "winerror", None) in (10053, 10054, 10038):
            return
        super().handle_error(request, client_address)

class Server:
    HOST = "localhost"
    PORT = 13337

    def __init__(self):
        self.server = None
        self.server_thread = None
        self.running = False

    def start(self):
        if self.running:
            print("[MCP] Server is already running")
            return

        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.running = True
        self.server_thread.start()

    def stop(self):
        if not self.running:
            return

        self.running = False
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.server_thread:
            self.server_thread.join()
            self.server = None
        print("[MCP] Server stopped")

    def _run_server(self):
        try:
                                                           
            self.server = MCPHTTPServer((Server.HOST, Server.PORT), JSONRPCRequestHandler)
            print(f"[MCP] Server started at http://{Server.HOST}:{Server.PORT}")
            self.server.serve_forever()
        except OSError as e:
            if e.errno == 98 or e.errno == 10048:                                       
                print("[MCP] Error: Port 13337 is already in use")
            else:
                print(f"[MCP] Server error: {e}")
            self.running = False
        except Exception as e:
            print(f"[MCP] Server error: {e}")
        finally:
            self.running = False

                                                        
           
                                                                                                                                
import logging
import queue
import traceback
import functools
from enum import IntEnum, IntFlag

import ida_hexrays
import ida_kernwin
import ida_funcs
import ida_gdl
import ida_lines
import ida_idaapi
import idc
import idaapi
import idautils
import ida_nalt
import ida_bytes
import ida_typeinf
import ida_xref
import ida_entry
import idautils
import ida_idd
import ida_dbg
import ida_name
import ida_ida
import ida_frame

ida_major, ida_minor = map(int, idaapi.get_kernel_version().split("."))

class IDAError(Exception):
    def __init__(self, message: str):
        super().__init__(message)

    @property
    def message(self) -> str:
        return self.args[0]

class IDASyncError(Exception):
    pass

                                                                             
                                                                       
 
          
          
 
          
 
            
                       
                                         
 
                
 
            
                       
                                   
 

logger = logging.getLogger(__name__)

                                            
class IDASafety(IntEnum):
    SAFE_NONE = ida_kernwin.MFF_FAST
    SAFE_READ = ida_kernwin.MFF_READ
    SAFE_WRITE = ida_kernwin.MFF_WRITE

call_stack = queue.LifoQueue()

def sync_wrapper(ff, safety_mode: IDASafety):
    """
    Call a function ff with a specific IDA safety_mode.
    """
                                                                          

    if safety_mode not in [IDASafety.SAFE_READ, IDASafety.SAFE_WRITE]:
        error_str = 'Invalid safety mode {} over function {}'\
                .format(safety_mode, ff.__name__)
        logger.error(error_str)
        raise IDASyncError(error_str)

                                
    res_container = queue.Queue()

    def runned():
                                      

                                                                  
        if not call_stack.empty():
            last_func_name = call_stack.get()
            error_str = ('Call stack is not empty while calling the '
                'function {} from {}').format(ff.__name__, last_func_name)
                                    
            raise IDASyncError(error_str)

        call_stack.put((ff.__name__))
        try:
            res_container.put(ff())
        except Exception as x:
            res_container.put(x)
        finally:
            call_stack.get()
                                            

    ret_val = idaapi.execute_sync(runned, safety_mode)
    res = res_container.get()
    if isinstance(res, Exception):
        raise res
    return res

def idawrite(f):
    """
    decorator for marking a function as modifying the IDB.
    schedules a request to be made in the main IDA loop to avoid IDB corruption.
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        ff = functools.partial(f, *args, **kwargs)
        ff.__name__ = f.__name__               
        return sync_wrapper(ff, idaapi.MFF_WRITE)
    return wrapper

def idaread(f):
    """
    decorator for marking a function as reading from the IDB.
    schedules a request to be made in the main IDA loop to avoid
      inconsistent results.
    MFF_READ constant via: http://www.openrce.org/forums/posts/1827
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        ff = functools.partial(f, *args, **kwargs)
        ff.__name__ = f.__name__               
        return sync_wrapper(ff, idaapi.MFF_READ)
    return wrapper

def is_window_active():
    """Returns whether IDA is currently active"""
    try:
        from PyQt5.QtWidgets import QApplication
    except ImportError:
        return False

    app = QApplication.instance()
    if app is None:
        return False

    for widget in app.topLevelWidgets():
        if widget.isActiveWindow():
            return True
    return False

class Metadata(TypedDict):
    path: str
    module: str
    base: str
    size: str
    md5: str
    sha256: str
    crc32: str
    filesize: str

def get_image_size() -> int:
    try:
                                                                                 
        info = idaapi.get_inf_structure()               
        omin_ea = info.omin_ea
        omax_ea = info.omax_ea
    except AttributeError:
        import ida_ida
        omin_ea = ida_ida.inf_get_omin_ea()
        omax_ea = ida_ida.inf_get_omax_ea()
                                                                                
    image_size = omax_ea - omin_ea
                                          
    header = idautils.peutils_t().header()
    if header and header[:4] == b"PE\0\0":
        image_size = struct.unpack("<I", header[0x50:0x54])[0]
    return image_size

@jsonrpc
def check_connection() -> dict:
    return {
        "status": "ok",
        "message": "MCP Server connected",
        "version": "2.0.0"
    }

@jsonrpc
@idaread
def get_metadata() -> Metadata:
    """Get metadata about the current IDB"""
                                                 
                                                       
    def hash(f):
        try:
            return f().hex()
        except:
            return ""

    return Metadata(path=idaapi.get_input_file_path(),
                    module=idaapi.get_root_filename(),
                    base=hex(idaapi.get_imagebase()),
                    size=hex(get_image_size()),
                    md5=hash(ida_nalt.retrieve_input_file_md5),
                    sha256=hash(ida_nalt.retrieve_input_file_sha256),
                    crc32=hex(ida_nalt.retrieve_input_file_crc32()),
                    filesize=hex(ida_nalt.retrieve_input_file_size()))

def get_prototype(fn: ida_funcs.func_t) -> Optional[str]:
    try:
        prototype: ida_typeinf.tinfo_t = fn.get_prototype()
        if prototype is not None:
            return str(prototype)
        else:
            return None
    except AttributeError:
        try:
            return idc.get_type(fn.start_ea)
        except:
            tif = ida_typeinf.tinfo_t()
            if ida_nalt.get_tinfo(tif, fn.start_ea):
                return str(tif)
            return None
    except Exception as e:
        print(f"Error getting function prototype: {e}")
        return None

class Function(TypedDict):
    address: str
    name: str
    size: str

def parse_address(address: str | int) -> int:
    if isinstance(address, int):
        return address
    try:
        return int(address, 0)
    except ValueError:
        for ch in address:
            if ch not in "0123456789abcdefABCDEF":
                raise IDAError(f"Failed to parse address: {address}")
        raise IDAError(f"Failed to parse address (missing 0x prefix): {address}")

@overload
def get_function(address: int, *, raise_error: Literal[True]) -> Function: ...

@overload
def get_function(address: int) -> Function: ...

@overload
def get_function(address: int, *, raise_error: Literal[False]) -> Optional[Function]: ...

def get_function(address, *, raise_error=True):
    fn = idaapi.get_func(address)
    if fn is None:
        if raise_error:
            raise IDAError(f"No function found at address {hex(address)}")
        return None

    try:
        name = fn.get_name()
    except AttributeError:
        name = ida_funcs.get_func_name(fn.start_ea)

    return Function(address=hex(address), name=name, size=hex(fn.end_ea - fn.start_ea))

DEMANGLED_TO_EA = {}

def create_demangled_to_ea_map():
    for ea in idautils.Functions():
                                               
                                                                
                                                              
                                 
        demangled = idaapi.demangle_name(
            idc.get_name(ea, 0), idaapi.MNG_NODEFINIT)
        if demangled:
            DEMANGLED_TO_EA[demangled] = ea

def get_type_by_name(type_name: str) -> ida_typeinf.tinfo_t:
                    
    if type_name in ('int8', '__int8', 'int8_t', 'char', 'signed char'):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_INT8)
    elif type_name in ('uint8', '__uint8', 'uint8_t', 'unsigned char', 'byte', 'BYTE'):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_UINT8)

                     
    elif type_name in ('int16', '__int16', 'int16_t', 'short', 'short int', 'signed short', 'signed short int'):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_INT16)
    elif type_name in ('uint16', '__uint16', 'uint16_t', 'unsigned short', 'unsigned short int', 'word', 'WORD'):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_UINT16)

                     
    elif type_name in ('int32', '__int32', 'int32_t', 'int', 'signed int', 'long', 'long int', 'signed long', 'signed long int'):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_INT32)
    elif type_name in ('uint32', '__uint32', 'uint32_t', 'unsigned int', 'unsigned long', 'unsigned long int', 'dword', 'DWORD'):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_UINT32)

                     
    elif type_name in ('int64', '__int64', 'int64_t', 'long long', 'long long int', 'signed long long', 'signed long long int'):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_INT64)
    elif type_name in ('uint64', '__uint64', 'uint64_t', 'unsigned int64', 'unsigned long long', 'unsigned long long int', 'qword', 'QWORD'):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_UINT64)

                      
    elif type_name in ('int128', '__int128', 'int128_t', '__int128_t'):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_INT128)
    elif type_name in ('uint128', '__uint128', 'uint128_t', '__uint128_t', 'unsigned int128'):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_UINT128)

                          
    elif type_name in ('float', ):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_FLOAT)
    elif type_name in ('double', ):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_DOUBLE)
    elif type_name in ('long double', 'ldouble'):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_LDOUBLE)

                  
    elif type_name in ('bool', '_Bool', 'boolean'):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_BOOL)

               
    elif type_name in ('void', ):
        return ida_typeinf.tinfo_t(ida_typeinf.BTF_VOID)

                                                     
    tif = ida_typeinf.tinfo_t()
    if tif.get_named_type(None, type_name, ida_typeinf.BTF_STRUCT):
        return tif

    if tif.get_named_type(None, type_name, ida_typeinf.BTF_TYPEDEF):
        return tif

    if tif.get_named_type(None, type_name, ida_typeinf.BTF_ENUM):
        return tif

    if tif.get_named_type(None, type_name, ida_typeinf.BTF_UNION):
        return tif

    if tif := ida_typeinf.tinfo_t(type_name):
        return tif

    raise IDAError(f"Unable to retrieve {type_name} type info object")

@jsonrpc
@idaread
def get_function_by_name(
    name: Annotated[str, "Name of the function to get"]
) -> Function:
    """Get a function by its name"""
    function_address = idaapi.get_name_ea(idaapi.BADADDR, name)
    if function_address == idaapi.BADADDR:
                                                    
        if len(DEMANGLED_TO_EA) == 0:
            create_demangled_to_ea_map()
                                                                  
        if name in DEMANGLED_TO_EA:
            function_address = DEMANGLED_TO_EA[name]
        else:
            raise IDAError(f"No function found with name {name}")
    return get_function(function_address)

@jsonrpc
@idaread
def get_function_by_address(
    address: Annotated[str, "Address of the function to get"],
) -> Function:
    """Get a function by its address"""
    return get_function(parse_address(address))

@jsonrpc
@idaread
def get_current_address() -> str:
    """Get the address currently selected by the user"""
    return hex(idaapi.get_screen_ea())

@jsonrpc
@idaread
def get_current_function() -> Optional[Function]:
    """Get the function currently selected by the user"""
    return get_function(idaapi.get_screen_ea())

class ConvertedNumber(TypedDict):
    decimal: str
    hexadecimal: str
    bytes: str
    ascii: Optional[str]
    binary: str

@jsonrpc
def convert_number(
    text: Annotated[str, "Textual representation of the number to convert"],
    size: Annotated[Optional[int], "Size of the variable in bytes"],
) -> ConvertedNumber:
    """Convert a number (decimal, hexadecimal) to different representations"""
    try:
        value = int(text, 0)
    except ValueError:
        raise IDAError(f"Invalid number: {text}")

                                     
    if not size:
        size = 0
        n = abs(value)
        while n:
            size += 1
            n >>= 1
        size += 7
        size //= 8

                                 
    try:
        bytes = value.to_bytes(size, "little", signed=True)
    except OverflowError:
        raise IDAError(f"Number {text} is too big for {size} bytes")

                                
    ascii = ""
    for byte in bytes.rstrip(b"\x00"):
        if byte >= 32 and byte <= 126:
            ascii += chr(byte)
        else:
            ascii = None
            break

    return ConvertedNumber(
        decimal=str(value),
        hexadecimal=hex(value),
        bytes=bytes.hex(" "),
        ascii=ascii,
        binary=bin(value),
    )

T = TypeVar("T")

class Page(TypedDict, Generic[T]):
    data: list[T]
    next_offset: Optional[int]

def paginate(data: list[T], offset: int, count: int) -> Page[T]:
    if count == 0:
        count = len(data)
    next_offset = offset + count
    if next_offset >= len(data):
        next_offset = None
    return {
        "data": data[offset:offset + count],
        "next_offset": next_offset,
    }

def pattern_filter(data: list[T], pattern: str, key: str) -> list[T]:
    if not pattern:
        return data

                                      

    def matches(item) -> bool:
        return pattern.lower() in item[key].lower()
    return list(filter(matches, data))

@jsonrpc
@idaread
def list_functions(
    offset: Annotated[int, "Offset to start listing from (start at 0)"],
    count: Annotated[int, "Number of functions to list (100 is a good default, 0 means remainder)"],
) -> Page[Function]:
    """List all functions in the database (paginated)"""
    functions = [get_function(address) for address in idautils.Functions()]
    return paginate(functions, offset, count)

class Global(TypedDict):
    address: str
    name: str

@jsonrpc
@idaread
def list_globals_filter(
    offset: Annotated[int, "Offset to start listing from (start at 0)"],
    count: Annotated[int, "Number of globals to list (100 is a good default, 0 means remainder)"],
    filter: Annotated[str, "Filter to apply to the list (required parameter, empty string for no filter). Case-insensitive contains or /regex/ syntax"],
) -> Page[Global]:
    """List matching globals in the database (paginated, filtered)"""
    globals: list[Global] = []
    for addr, name in idautils.Names():
                        
        if not idaapi.get_func(addr):
            globals += [Global(address=hex(addr), name=name)]

    globals = pattern_filter(globals, filter, "name")
    return paginate(globals, offset, count)

@jsonrpc
def list_globals(
    offset: Annotated[int, "Offset to start listing from (start at 0)"],
    count: Annotated[int, "Number of globals to list (100 is a good default, 0 means remainder)"],
) -> Page[Global]:
    """List all globals in the database (paginated)"""
    return list_globals_filter(offset, count, "")

class Import(TypedDict):
    address: str
    imported_name: str
    module: str

@jsonrpc
@idaread
def list_imports(
        offset: Annotated[int, "Offset to start listing from (start at 0)"],
        count: Annotated[int, "Number of imports to list (100 is a good default, 0 means remainder)"],
) -> Page[Import]:
    """ List all imported symbols with their name and module (paginated) """
    nimps = ida_nalt.get_import_module_qty()

    rv = []
    for i in range(nimps):
        module_name = ida_nalt.get_import_module_name(i)
        if not module_name:
            module_name = "<unnamed>"

        def imp_cb(ea, symbol_name, ordinal, acc):
            if not symbol_name:
                symbol_name = f"#{ordinal}"

            acc += [Import(address=hex(ea), imported_name=symbol_name, module=module_name)]

            return True

        imp_cb_w_context = lambda ea, symbol_name, ordinal: imp_cb(ea, symbol_name, ordinal, rv)
        ida_nalt.enum_import_names(i, imp_cb_w_context)

    return paginate(rv, offset, count)

class String(TypedDict):
    address: str
    length: int
    string: str

@jsonrpc
@idaread
def list_strings_filter(
    offset: Annotated[int, "Offset to start listing from (start at 0)"],
    count: Annotated[int, "Number of strings to list (100 is a good default, 0 means remainder)"],
    filter: Annotated[str, "Filter to apply to the list (required parameter, empty string for no filter). Case-insensitive contains or /regex/ syntax"],
) -> Page[String]:
    """List matching strings in the database (paginated, filtered)"""
    strings: list[String] = []
    for item in idautils.Strings():
        if item is None:
            continue
        try:
            string = str(item)
            if string:
                strings += [
                    String(address=hex(item.ea), length=item.length, string=string),
                ]
        except:
            continue
    strings = pattern_filter(strings, filter, "string")
    return paginate(strings, offset, count)

@jsonrpc
def list_strings(
    offset: Annotated[int, "Offset to start listing from (start at 0)"],
    count: Annotated[int, "Number of strings to list (100 is a good default, 0 means remainder)"],
) -> Page[String]:
    """List all strings in the database (paginated)"""
    return list_strings_filter(offset, count, "")

@jsonrpc
@idaread
def list_local_types():
    """List all Local types in the database"""
    error = ida_hexrays.hexrays_failure_t()
    locals = []
    idati = ida_typeinf.get_idati()
    type_count = ida_typeinf.get_ordinal_limit(idati)
    for ordinal in range(1, type_count):
        try:
            tif = ida_typeinf.tinfo_t()
            if tif.get_numbered_type(idati, ordinal):
                type_name = tif.get_type_name()
                if not type_name:
                    type_name = f"<Anonymous Type #{ordinal}>"
                locals.append(f"\nType #{ordinal}: {type_name}")
                if tif.is_udt():
                    c_decl_flags = (ida_typeinf.PRTYPE_MULTI | ida_typeinf.PRTYPE_TYPE | ida_typeinf.PRTYPE_SEMI | ida_typeinf.PRTYPE_DEF | ida_typeinf.PRTYPE_METHODS | ida_typeinf.PRTYPE_OFFSETS)
                    c_decl_output = tif._print(None, c_decl_flags)
                    if c_decl_output:
                        locals.append(f"  C declaration:\n{c_decl_output}")
                else:
                    simple_decl = tif._print(None, ida_typeinf.PRTYPE_1LINE | ida_typeinf.PRTYPE_TYPE | ida_typeinf.PRTYPE_SEMI)
                    if simple_decl:
                        locals.append(f"  Simple declaration:\n{simple_decl}")
            else:
                message = f"\nType #{ordinal}: Failed to retrieve information."
                if error.str:
                    message += f": {error.str}"
                if error.errea != idaapi.BADADDR:
                    message += f"from (address: {hex(error.errea)})"
                raise IDAError(message)
        except:
            continue
    return locals

def decompile_checked(address: int) -> ida_hexrays.cfunc_t:
    if not ida_hexrays.init_hexrays_plugin():
        raise IDAError("Hex-Rays decompiler is not available")
    error = ida_hexrays.hexrays_failure_t()
    cfunc = ida_hexrays.decompile_func(address, error, ida_hexrays.DECOMP_WARNINGS)
    if not cfunc:
        if error.code == ida_hexrays.MERR_LICENSE:
            raise IDAError("Decompiler license is not available. Use `disassemble_function` to get the assembly code instead.")

        message = f"Decompilation failed at {hex(address)}"
        if error.str:
            message += f": {error.str}"
        if error.errea != idaapi.BADADDR:
            message += f" (address: {hex(error.errea)})"
        raise IDAError(message)
    return cfunc                                      

@jsonrpc
@idaread
def decompile_function(
    address: Annotated[str, "Address of the function to decompile"],
) -> str:
    """Decompile a function at the given address"""
    start = parse_address(address)
    cfunc = decompile_checked(start)
    if is_window_active():
        ida_hexrays.open_pseudocode(start, ida_hexrays.OPF_REUSE)
    sv = cfunc.get_pseudocode()
    pseudocode = ""
    for i, sl in enumerate(sv):
        sl: ida_kernwin.simpleline_t
        item = ida_hexrays.ctree_item_t()
        addr = None if i > 0 else cfunc.entry_ea
        if cfunc.get_line_item(sl.line, 0, False, None, item, None):                                         
            dstr: str | None = item.dstr()
            if dstr:
                ds = dstr.split(": ")
                if len(ds) == 2:
                    try:
                        addr = int(ds[0], 16)
                    except ValueError:
                        pass
        line = ida_lines.tag_remove(sl.line)
        if len(pseudocode) > 0:
            pseudocode += "\n"
        if not addr:
            pseudocode += f"/* line: {i} */ {line}"
        else:
            pseudocode += f"/* line: {i}, address: {hex(addr)} */ {line}"

    return pseudocode

class DisassemblyLine(TypedDict):
    segment: NotRequired[str]
    address: str
    label: NotRequired[str]
    instruction: str
    comments: NotRequired[list[str]]

class Argument(TypedDict):
    name: str
    type: str

class StackFrameVariable(TypedDict):
    name: str
    offset: str
    size: str
    type: str

class DisassemblyFunction(TypedDict):
    name: str
    start_ea: str
    return_type: NotRequired[str]
    arguments: NotRequired[list[Argument]]
    stack_frame: list[StackFrameVariable]
    lines: list[DisassemblyLine]

@jsonrpc
@idaread
def disassemble_function(
    start_address: Annotated[str, "Address of the function to disassemble"],
) -> DisassemblyFunction:
    """Get assembly code for a function (API-compatible with older IDA builds)"""
    start = parse_address(start_address)
    func = idaapi.get_func(start)
    if not func:
        raise IDAError(f"No function found at address {hex(start)}")
    if is_window_active():
        ida_kernwin.jumpto(start)

    func_name: str = ida_funcs.get_func_name(func.start_ea) or "<unnamed>"

    lines: list[DisassemblyLine] = []
    for ea in idautils.FuncItems(func.start_ea):
        if ea == idaapi.BADADDR:
            continue

        seg = idaapi.getseg(ea)
        segment: str | None = idaapi.get_segm_name(seg) if seg else None

        label: str | None = idc.get_name(ea, 0)
        if not label or (label == func_name and ea == func.start_ea):
            label = None

        comments: list[str] = []
        c: str | None = idaapi.get_cmt(ea, False)
        if c:
            comments.append(c)
        c = idaapi.get_cmt(ea, True)
        if c:
            comments.append(c)

        mnem: str = idc.print_insn_mnem(ea) or ""
        ops: list[str] = []
        for n in range(8):
            if idc.get_operand_type(ea, n) == idaapi.o_void:
                break
            ops.append(idc.print_operand(ea, n) or "")
        instruction = f"{mnem} {', '.join(ops)}".rstrip()

        line: DisassemblyLine = {
            "address": hex(ea),
            "instruction": instruction
        }
        if segment:
            line["segment"] = segment
        if label:
            line["label"] = label
        if comments:
            line["comments"] = comments
        lines.append(line)

                                                         
    rettype = None
    args: Optional[list[Argument]] = None
    tif = ida_typeinf.tinfo_t()
    if ida_nalt.get_tinfo(tif, func.start_ea) and tif.is_func():
        ftd = ida_typeinf.func_type_data_t()
        if tif.get_func_details(ftd):
            rettype = str(ftd.rettype)
            args = [Argument(name=(a.name or f"arg{i}"), type=str(a.type))
                    for i, a in enumerate(ftd)]

    out: DisassemblyFunction = {
        "name": func_name,
        "start_ea": hex(func.start_ea),
        "stack_frame": get_stack_frame_variables_internal(func.start_ea, False),
        "lines": lines,
    }
    if rettype:
        out["return_type"] = rettype
    if args is not None:
        out["arguments"] = args
    return out

class Xref(TypedDict):
    address: str
    type: str
    function: Optional[Function]

@jsonrpc
@idaread
def get_xrefs_to(
    address: Annotated[str, "Address to get cross references to"],
) -> list[Xref]:
    """Get all cross references to the given address"""
    xrefs = []
    xref: ida_xref.xrefblk_t
    for xref in idautils.XrefsTo(parse_address(address)):                                                  
        xrefs += [
            Xref(address=hex(xref.frm),
                 type="code" if xref.iscode else "data",
                 function=get_function(xref.frm, raise_error=False))
        ]
    return xrefs

@jsonrpc
@idaread
def get_xrefs_to_field(
    struct_name: Annotated[str, "Name of the struct (type) containing the field"],
    field_name: Annotated[str, "Name of the field (member) to get xrefs to"],
) -> list[Xref]:
    """Get all cross references to a named struct field (member)"""

                          
    til = ida_typeinf.get_idati()
    if not til:
        raise IDAError("Failed to retrieve type library.")

                                 
    tif = ida_typeinf.tinfo_t()
    if not tif.get_named_type(til, struct_name, ida_typeinf.BTF_STRUCT, True, False):
        print(f"Structure '{struct_name}' not found.")
        return []

                         
    idx = ida_typeinf.get_udm_by_fullname(None, struct_name + '.' + field_name)                                                  
    if idx == -1:
        print(f"Field '{field_name}' not found in structure '{struct_name}'.")
        return []

                             
    tid = tif.get_udm_tid(idx)
    if tid == ida_idaapi.BADADDR:
        raise IDAError(f"Unable to get tid for structure '{struct_name}' and field '{field_name}'.")

                          
    xrefs = []
    xref: ida_xref.xrefblk_t
    for xref in idautils.XrefsTo(tid):                                                  
        xrefs += [
            Xref(address=hex(xref.frm),
                 type="code" if xref.iscode else "data",
                 function=get_function(xref.frm, raise_error=False))
        ]
    return xrefs

@jsonrpc
@idaread
def get_callees(
    function_address: Annotated[str, "Address of the function to get callee functions"],
) -> list[dict[str, str]]:
    """Get all the functions called (callees) by the function at function_address"""
    func_start = parse_address(function_address)
    func = idaapi.get_func(func_start)
    if not func:
        raise IDAError(f"No function found containing address {function_address}")
    func_end = idc.find_func_end(func_start)
    callees: list[dict[str, str]] = []
    current_ea = func_start
    while current_ea < func_end:
        insn = idaapi.insn_t()
        idaapi.decode_insn(insn, current_ea)
        if insn.itype in [idaapi.NN_call, idaapi.NN_callfi, idaapi.NN_callni]:
            target = idc.get_operand_value(current_ea, 0)
            target_type = idc.get_operand_type(current_ea, 0)
                                                                                  
            if target_type in [idaapi.o_mem, idaapi.o_near, idaapi.o_far]:
                                                                                                  
                                                                               
                func_type = (
                    "internal" if idaapi.get_func(target) is not None else "external"
                )
                func_name = idc.get_name(target)
                if func_name is not None:
                    callees.append(
                        {"address": hex(target), "name": func_name, "type": func_type}
                    )
        current_ea = idc.next_head(current_ea, func_end)

                         
    unique_callee_tuples = {tuple(callee.items()) for callee in callees}
    unique_callees = [dict(callee) for callee in unique_callee_tuples]
    return unique_callees                

@jsonrpc
@idaread
def get_callers(
    function_address: Annotated[str, "Address of the function to get callers"],
) -> list[Function]:
    """Get all callers of the given address"""
    callers = {}
    for caller_address in idautils.CodeRefsTo(parse_address(function_address), 0):
                                                 
        func = get_function(caller_address, raise_error=False)
        if not func:
            continue
                                                  
        insn = idaapi.insn_t()
        idaapi.decode_insn(insn, caller_address)
                                         
        if insn.itype not in [idaapi.NN_call, idaapi.NN_callfi, idaapi.NN_callni]:
            continue
                                        
        callers[func["address"]] = func

    return list(callers.values())

@jsonrpc
@idaread
def get_entry_points() -> list[Function]:
    """Get all entry points in the database"""
    result = []
    for i in range(ida_entry.get_entry_qty()):
        ordinal = ida_entry.get_entry_ordinal(i)
        address = ida_entry.get_entry(ordinal)
        func = get_function(address, raise_error=False)
        if func is not None:
            result.append(func)
    return result

@jsonrpc
@idawrite
def set_comment(
    address: Annotated[str, "Address in the function to set the comment for"],
    comment: Annotated[str, "Comment text"],
):
    """Set a comment for a given address in the function disassembly and pseudocode"""
    ea = parse_address(address)

    if not idaapi.set_cmt(ea, comment, False):
        raise IDAError(f"Failed to set disassembly comment at {hex(ea)}")

    if not ida_hexrays.init_hexrays_plugin():
        return

                                                                                   
                                                
    try:
        cfunc = decompile_checked(ea)
    except IDAError:
                                                        
        return

                                              
    if ea == cfunc.entry_ea:
        idc.set_func_cmt(ea, comment, True)
        cfunc.refresh_func_ctext()
        return

    eamap = cfunc.get_eamap()
    if ea not in eamap:
        print(f"Failed to set decompiler comment at {hex(ea)}")
        return
    nearest_ea = eamap[ea][0].ea

                                     
    if cfunc.has_orphan_cmts():
        cfunc.del_orphan_cmts()
        cfunc.save_user_cmts()

                                                       
    tl = idaapi.treeloc_t()
    tl.ea = nearest_ea
    for itp in range(idaapi.ITP_SEMI, idaapi.ITP_COLON):
        tl.itp = itp
        cfunc.set_user_cmt(tl, comment)
        cfunc.save_user_cmts()
        cfunc.refresh_func_ctext()
        if not cfunc.has_orphan_cmts():
            return
        cfunc.del_orphan_cmts()
        cfunc.save_user_cmts()
    print(f"Failed to set decompiler comment at {hex(ea)}")

def refresh_decompiler_widget():
    widget = ida_kernwin.get_current_widget()
    if widget is not None:
        vu = ida_hexrays.get_widget_vdui(widget)
        if vu is not None:
            vu.refresh_ctext()

def refresh_decompiler_ctext(function_address: int):
    error = ida_hexrays.hexrays_failure_t()
    cfunc: ida_hexrays.cfunc_t = ida_hexrays.decompile_func(function_address, error, ida_hexrays.DECOMP_WARNINGS)
    if cfunc:
        cfunc.refresh_func_ctext()

@jsonrpc
@idawrite
def rename_local_variable(
    function_address: Annotated[str, "Address of the function containing the variable"],
    old_name: Annotated[str, "Current name of the variable"],
    new_name: Annotated[str, "New name for the variable (empty for a default name)"],
):
    """Rename a local variable in a function"""
    func = idaapi.get_func(parse_address(function_address))
    if not func:
        raise IDAError(f"No function found at address {function_address}")
    if not ida_hexrays.rename_lvar(func.start_ea, old_name, new_name):
        raise IDAError(f"Failed to rename local variable {old_name} in function {hex(func.start_ea)}")
    refresh_decompiler_ctext(func.start_ea)

@jsonrpc
@idawrite
def rename_global_variable(
    old_name: Annotated[str, "Current name of the global variable"],
    new_name: Annotated[str, "New name for the global variable (empty for a default name)"],
):
    """Rename a global variable"""
    ea = idaapi.get_name_ea(idaapi.BADADDR, old_name)
    if not idaapi.set_name(ea, new_name):
        raise IDAError(f"Failed to rename global variable {old_name} to {new_name}")
    refresh_decompiler_ctext(ea)

@jsonrpc
@idawrite
def set_global_variable_type(
    variable_name: Annotated[str, "Name of the global variable"],
    new_type: Annotated[str, "New type for the variable"],
):
    """Set a global variable's type"""
    ea = idaapi.get_name_ea(idaapi.BADADDR, variable_name)
    tif = get_type_by_name(new_type)
    if not tif:
        raise IDAError(f"Parsed declaration is not a variable type")
    if not ida_typeinf.apply_tinfo(ea, tif, ida_typeinf.PT_SIL):
        raise IDAError(f"Failed to apply type")

def patch_address_assemble(
    ea: int,
    assemble: str,
) -> int:
    """Patch Address Assemble with ARM64 and Hex/String support"""
    assemble_clean = assemble.strip()
    if assemble_clean.lower() == "nop":
        info = idaapi.get_inf_structure()
        proc = info.procname.lower() if hasattr(info, "procname") else ""
        if "arm" in proc or "aarch64" in proc or idaapi.get_processor_name().lower().startswith("arm"):
            bytes_to_patch = b"\x1f\x20\x03\xd5"
        else:
            bytes_to_patch = b"\x90"
        check_assemble = True
    elif assemble_clean.lower().startswith("hex:"):
        clean_hex = assemble_clean[4:].replace(" ", "").replace("0x", "").replace(",", "")
        bytes_to_patch = bytes.fromhex(clean_hex)
        check_assemble = True
    elif assemble_clean.lower().startswith("str:"):
        bytes_to_patch = assemble_clean[4:].encode("utf-8")
        check_assemble = True
    else:
        (check_assemble, bytes_to_patch) = idautils.Assemble(ea, assemble)
    if check_assemble == False:
        raise IDAError(f"Failed to assemble instruction: {assemble}")
    try:
        ida_bytes.patch_bytes(ea, bytes_to_patch)
    except:
        raise IDAError(f"Failed to patch bytes at address {hex(ea)}")

    return len(bytes_to_patch)

@jsonrpc
@idawrite
def patch_bytes(
    address: Annotated[str, "Address to patch bytes at"],
    data: Annotated[str, "Hex bytes string (e.g. '1F 20 03 D5') or 'str:your_string'"],
) -> str:
    """Patch raw bytes or string into the IDB"""
    ea = parse_address(address)
    s = data.strip()
    if s.startswith("str:"):
        b = s[4:].encode("utf-8")
    else:
        clean_hex = s.replace(" ", "").replace("0x", "").replace(",", "")
        b = bytes.fromhex(clean_hex)
    try:
        ida_bytes.patch_bytes(ea, b)
    except Exception as e:
        raise IDAError(f"Failed to patch bytes at {hex(ea)}: {e}")
    return f"Successfully patched {len(b)} bytes at {hex(ea)}"

@jsonrpc
@idawrite
def patch_address_assembles(
    address: Annotated[str, "Starting Address to apply patch"],
    instructions: Annotated[str, "Assembly instructions separated by ';'"],
) -> str:
    ea = parse_address(address)
    assembles = instructions.split(";")
    for assemble in assembles:
        assemble = assemble.strip()
        try:
            patch_bytes_len = patch_address_assemble(ea, assemble)
        except IDAError as e:
            raise IDAError(f"Failed to patch bytes at address {hex(ea)}: {e}")
        ea += patch_bytes_len
    return f"Patched {len(assembles)} instructions"

@jsonrpc
@idaread
def get_global_variable_value_by_name(variable_name: Annotated[str, "Name of the global variable"]) -> str:
    """
    Read a global variable's value (if known at compile-time)

    Prefer this function over the `data_read_*` functions.
    """
    ea = idaapi.get_name_ea(idaapi.BADADDR, variable_name)
    if ea == idaapi.BADADDR:
        raise IDAError(f"Global variable {variable_name} not found")

    return get_global_variable_value_internal(ea)

@jsonrpc
@idaread
def get_global_variable_value_at_address(address: Annotated[str, "Address of the global variable"]) -> str:
    """
    Read a global variable's value by its address (if known at compile-time)

    Prefer this function over the `data_read_*` functions.
    """
    ea = parse_address(address)
    return get_global_variable_value_internal(ea)

def get_global_variable_value_internal(ea: int) -> str:
                                                
     tif = ida_typeinf.tinfo_t()
     if not ida_nalt.get_tinfo(tif, ea):
                                                                     
         if not ida_bytes.has_any_name(ea):
             raise IDAError(f"Failed to get type information for variable at {ea:#x}")

         size = ida_bytes.get_item_size(ea)
         if size == 0:
             raise IDAError(f"Failed to get type information for variable at {ea:#x}")
     else:
                                             
         size = tif.get_size()

                                       
     if size == 0 and tif.is_array() and tif.get_array_element().is_decl_char():
         return_string = idaapi.get_strlit_contents(ea, -1, 0).decode("utf-8").strip()
         return f"\"{return_string}\""
     elif size == 1:
         return hex(ida_bytes.get_byte(ea))
     elif size == 2:
         return hex(ida_bytes.get_word(ea))
     elif size == 4:
         return hex(ida_bytes.get_dword(ea))
     elif size == 8:
         return hex(ida_bytes.get_qword(ea))
     else:
                                                
         return ' '.join(hex(x) for x in ida_bytes.get_bytes(ea, size))

@jsonrpc
@idawrite
def rename_function(
    function_address: Annotated[str, "Address of the function to rename"],
    new_name: Annotated[str, "New name for the function (empty for a default name)"],
):
    """Rename a function"""
    func = idaapi.get_func(parse_address(function_address))
    if not func:
        raise IDAError(f"No function found at address {function_address}")
    if not idaapi.set_name(func.start_ea, new_name):
        raise IDAError(f"Failed to rename function {hex(func.start_ea)} to {new_name}")
    refresh_decompiler_ctext(func.start_ea)

@jsonrpc
@idawrite
def set_function_prototype(
    function_address: Annotated[str, "Address of the function"],
    prototype: Annotated[str, "New function prototype"],
):
    """Set a function's prototype"""
    func = idaapi.get_func(parse_address(function_address))
    if not func:
        raise IDAError(f"No function found at address {function_address}")
    try:
        tif = ida_typeinf.tinfo_t(prototype, None, ida_typeinf.PT_SIL)
        if not tif.is_func():
            raise IDAError(f"Parsed declaration is not a function type")
        if not ida_typeinf.apply_tinfo(func.start_ea, tif, ida_typeinf.PT_SIL):
            raise IDAError(f"Failed to apply type")
        refresh_decompiler_ctext(func.start_ea)
    except Exception as e:
        raise IDAError(f"Failed to parse prototype string: {prototype}")

class my_modifier_t(ida_hexrays.user_lvar_modifier_t):
    def __init__(self, var_name: str, new_type: ida_typeinf.tinfo_t):
        ida_hexrays.user_lvar_modifier_t.__init__(self)
        self.var_name = var_name
        self.new_type = new_type

    def modify_lvars(self, lvinf):
        for lvar_saved in lvinf.lvvec:
            lvar_saved: ida_hexrays.lvar_saved_info_t
            if lvar_saved.name == self.var_name:
                lvar_saved.type = self.new_type
                return True
        return False

                                                                       
def parse_decls_ctypes(decls: str, hti_flags: int) -> tuple[int, list[str]]:
    if sys.platform == "win32":
        import ctypes

        assert isinstance(decls, str), "decls must be a string"
        assert isinstance(hti_flags, int), "hti_flags must be an int"
        c_decls = decls.encode("utf-8")
        c_til = None
        ida_dll = ctypes.CDLL("ida")
        ida_dll.parse_decls.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        ida_dll.parse_decls.restype = ctypes.c_int

        messages: list[str] = []

        @ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p)
        def magic_printer(fmt: bytes, arg1: bytes):
            if fmt.count(b"%") == 1 and b"%s" in fmt:
                formatted = fmt.replace(b"%s", arg1)
                messages.append(formatted.decode("utf-8"))
                return len(formatted) + 1
            else:
                messages.append(f"unsupported magic_printer fmt: {repr(fmt)}")
                return 0

        errors = ida_dll.parse_decls(c_til, c_decls, magic_printer, hti_flags)
    else:
                                                                               
                                                                       
        errors = ida_typeinf.parse_decls(None, decls, False, hti_flags)
        messages = []
    return errors, messages

@jsonrpc
@idawrite
def declare_c_type(
    c_declaration: Annotated[str, "C declaration of the type. Examples include: typedef int foo_t; struct bar { int a; bool b; };"],
):
    """Create or update a local type from a C declaration"""
                                                                           
                                                     
                                                         
    flags = ida_typeinf.PT_SIL | ida_typeinf.PT_EMPTY | ida_typeinf.PT_TYP
    errors, messages = parse_decls_ctypes(c_declaration, flags)

    pretty_messages = "\n".join(messages)
    if errors > 0:
        raise IDAError(f"Failed to parse type:\n{c_declaration}\n\nErrors:\n{pretty_messages}")
    return f"success\n\nInfo:\n{pretty_messages}"

@jsonrpc
@idawrite
def set_local_variable_type(
    function_address: Annotated[str, "Address of the decompiled function containing the variable"],
    variable_name: Annotated[str, "Name of the variable"],
    new_type: Annotated[str, "New type for the variable"],
):
    """Set a local variable's type"""
    try:
                                                             
        new_tif = ida_typeinf.tinfo_t(new_type, None, ida_typeinf.PT_SIL)
    except Exception:
        try:
            new_tif = ida_typeinf.tinfo_t()
                                                        
            ida_typeinf.parse_decl(new_tif, None, new_type + ";", ida_typeinf.PT_SIL)                                                  
        except Exception:
            raise IDAError(f"Failed to parse type: {new_type}")
    func = idaapi.get_func(parse_address(function_address))
    if not func:
        raise IDAError(f"No function found at address {function_address}")
    if not ida_hexrays.rename_lvar(func.start_ea, variable_name, variable_name):
        raise IDAError(f"Failed to find local variable: {variable_name}")
    modifier = my_modifier_t(variable_name, new_tif)
    if not ida_hexrays.modify_user_lvars(func.start_ea, modifier):
        raise IDAError(f"Failed to modify local variable: {variable_name}")
    refresh_decompiler_ctext(func.start_ea)

@jsonrpc
@idaread
def get_stack_frame_variables(
        function_address: Annotated[str, "Address of the disassembled function to retrieve the stack frame variables"]
) -> list[StackFrameVariable]:
    """ Retrieve the stack frame variables for a given function """
    return get_stack_frame_variables_internal(parse_address(function_address), True)

def get_stack_frame_variables_internal(function_address: int, raise_error: bool) -> list[StackFrameVariable]:
                                                        
    if ida_major < 9:
        return []

    func = idaapi.get_func(function_address)
    if not func:
        if raise_error:
            raise IDAError(f"No function found at address {function_address}")
        return []

    tif = ida_typeinf.tinfo_t()
    if not tif.get_type_by_tid(func.frame) or not tif.is_udt():
        return []

    members: list[StackFrameVariable] = []
    udt = ida_typeinf.udt_type_data_t()
    tif.get_udt_details(udt)
    for udm in udt:
        if not udm.is_gap():
            name = udm.name
            offset = udm.offset // 8
            size = udm.size // 8
            type = str(udm.type)
            members.append(StackFrameVariable(
                name=name,
                offset=hex(offset),
                size=hex(size),
                type=type
            ))
    return members

class StructureMember(TypedDict):
    name: str
    offset: str
    size: str
    type: str

class StructureDefinition(TypedDict):
    name: str
    size: str
    members: list[StructureMember]

@jsonrpc
@idaread
def get_defined_structures() -> list[StructureDefinition]:
    """ Returns a list of all defined structures """

    rv = []
    limit = ida_typeinf.get_ordinal_limit()
    for ordinal in range(1, limit):
        tif = ida_typeinf.tinfo_t()
        tif.get_numbered_type(None, ordinal)
        if tif.is_udt():
            udt = ida_typeinf.udt_type_data_t()
            members = []
            if tif.get_udt_details(udt):
                members = [
                    StructureMember(name=x.name,
                                    offset=hex(x.offset // 8),
                                    size=hex(x.size // 8),
                                    type=str(x.type))
                    for _, x in enumerate(udt)
                ]

            rv += [StructureDefinition(name=tif.get_type_name(),                                                  
                                       size=hex(tif.get_size()),
                                       members=members)]

    return rv

@jsonrpc
@idaread
def analyze_struct_detailed(name: Annotated[str, "Name of the structure to analyze"]) -> dict:
    """Detailed analysis of a structure with all fields"""
                      
    tif = ida_typeinf.tinfo_t()
    if not tif.get_named_type(None, name):
        raise IDAError(f"Structure '{name}' not found!")

    result = {
        "name": name,
        "type": str(tif._print()),
        "size": tif.get_size(),
        "is_udt": tif.is_udt()
    }

    if not tif.is_udt():
        result["error"] = "This is not a user-defined type!"
        return result

                                         
    udt_data = ida_typeinf.udt_type_data_t()
    if not tif.get_udt_details(udt_data):
        result["error"] = "Failed to get structure details!"
        return result

    result["member_count"] = udt_data.size()
    result["is_union"] = udt_data.is_union
    result["udt_type"] = "Union" if udt_data.is_union else "Struct"

                                         
    members = []
    for i, member in enumerate(udt_data):
        offset = member.begin() // 8                         
        size = member.size // 8 if member.size > 0 else member.type.get_size()
        member_type = member.type._print()
        member_name = member.name

        member_info = {
            "index": i,
            "offset": f"0x{offset:08X}",
            "size": size,
            "type": member_type,
            "name": member_name,
            "is_nested_udt": member.type.is_udt()
        }

                                                                    
        if member.type.is_udt():
            member_info["nested_size"] = member.type.get_size()

        members.append(member_info)

    result["members"] = members
    result["total_size"] = tif.get_size()

    return result

@jsonrpc
@idaread
def get_struct_at_address(address: Annotated[str, "Address to analyze structure at"],
                         struct_name: Annotated[str, "Name of the structure"]) -> dict:
    """Get structure field values at a specific address"""
    addr = parse_address(address)

                         
    tif = ida_typeinf.tinfo_t()
    if not tif.get_named_type(None, struct_name):
        raise IDAError(f"Structure '{struct_name}' not found!")

                           
    udt_data = ida_typeinf.udt_type_data_t()
    if not tif.get_udt_details(udt_data):
        raise IDAError("Failed to get structure details!")

    result = {
        "struct_name": struct_name,
        "address": f"0x{addr:X}",
        "members": []
    }

    for member in udt_data:
        offset = member.begin() // 8
        member_addr = addr + offset
        member_type = member.type._print()
        member_name = member.name
        member_size = member.type.get_size()

                                        
        try:
            if member.type.is_ptr():
                         
                is_64bit = ida_ida.inf_is_64bit() if ida_major >= 9 else idaapi.get_inf_structure().is_64bit()
                if is_64bit:
                    value = idaapi.get_qword(member_addr)
                    value_str = f"0x{value:016X}"
                else:
                    value = idaapi.get_dword(member_addr)
                    value_str = f"0x{value:08X}"
            elif member_size == 1:
                value = idaapi.get_byte(member_addr)
                value_str = f"0x{value:02X} ({value})"
            elif member_size == 2:
                value = idaapi.get_word(member_addr)
                value_str = f"0x{value:04X} ({value})"
            elif member_size == 4:
                value = idaapi.get_dword(member_addr)
                value_str = f"0x{value:08X} ({value})"
            elif member_size == 8:
                value = idaapi.get_qword(member_addr)
                value_str = f"0x{value:016X} ({value})"
            else:
                                                            
                bytes_data = []
                for i in range(min(member_size, 16)):
                    try:
                        byte_val = idaapi.get_byte(member_addr + i)
                        bytes_data.append(f"{byte_val:02X}")
                    except:
                        break
                value_str = f"[{' '.join(bytes_data)}{'...' if member_size > 16 else ''}]"
        except:
            value_str = "<failed to read>"

        member_info = {
            "offset": f"0x{offset:08X}",
            "type": member_type,
            "name": member_name,
            "value": value_str
        }

        result["members"].append(member_info)

    return result

@jsonrpc
@idaread
def get_struct_info_simple(name: Annotated[str, "Name of the structure"]) -> dict:
    """Simple function to get basic structure information"""
    tif = ida_typeinf.tinfo_t()
    if not tif.get_named_type(None, name):
        raise IDAError(f"Structure '{name}' not found!")

    info = {
        'name': name,
        'type': tif._print(),
        'size': tif.get_size(),
        'is_udt': tif.is_udt()
    }

    if tif.is_udt():
        udt_data = ida_typeinf.udt_type_data_t()
        if tif.get_udt_details(udt_data):
            info['member_count'] = udt_data.size()
            info['is_union'] = udt_data.is_union

            members = []
            for member in udt_data:
                members.append({
                    'name': member.name,
                    'type': member.type._print(),
                    'offset': member.begin() // 8,
                    'size': member.type.get_size()
                })
            info['members'] = members

    return info

@jsonrpc
@idaread
def search_structures(filter: Annotated[str, "Filter pattern to search for structures (case-insensitive)"]) -> list[dict]:
    """Search for structures by name pattern"""
    results = []
    limit = ida_typeinf.get_ordinal_limit()

    for ordinal in range(1, limit):
        tif = ida_typeinf.tinfo_t()
        if tif.get_numbered_type(None, ordinal):
            type_name: str = tif.get_type_name()                                                  
            if type_name and filter.lower() in type_name.lower():
                if tif.is_udt():
                    udt_data = ida_typeinf.udt_type_data_t()
                    member_count = 0
                    if tif.get_udt_details(udt_data):
                        member_count = udt_data.size()

                    results.append({
                        "name": type_name,
                        "size": tif.get_size(),
                        "member_count": member_count,
                        "is_union": udt_data.is_union if tif.get_udt_details(udt_data) else False,
                        "ordinal": ordinal
                    })

    return results

@jsonrpc
@idawrite
def rename_stack_frame_variable(
        function_address: Annotated[str, "Address of the disassembled function to set the stack frame variables"],
        old_name: Annotated[str, "Current name of the variable"],
        new_name: Annotated[str, "New name for the variable (empty for a default name)"]
):
    """ Change the name of a stack variable for an IDA function """
    func = idaapi.get_func(parse_address(function_address))
    if not func:
        raise IDAError(f"No function found at address {function_address}")

    frame_tif = ida_typeinf.tinfo_t()
    if not ida_frame.get_func_frame(frame_tif, func):
        raise IDAError("No frame returned.")

    idx, udm = frame_tif.get_udm(old_name)                                                  
    if not udm:
        raise IDAError(f"{old_name} not found.")

    tid = frame_tif.get_udm_tid(idx)
    if ida_frame.is_special_frame_member(tid):
        raise IDAError(f"{old_name} is a special frame member. Will not change the name.")

    udm = ida_typeinf.udm_t()
    frame_tif.get_udm_by_tid(udm, tid)
    offset = udm.offset // 8
    if ida_frame.is_funcarg_off(func, offset):
        raise IDAError(f"{old_name} is an argument member. Will not change the name.")

    sval = ida_frame.soff_to_fpoff(func, offset)
    if not ida_frame.define_stkvar(func, new_name, sval, udm.type):
        raise IDAError("failed to rename stack frame variable")

@jsonrpc
@idawrite
def create_stack_frame_variable(
        function_address: Annotated[str, "Address of the disassembled function to set the stack frame variables"],
        offset: Annotated[str, "Offset of the stack frame variable"],
        variable_name: Annotated[str, "Name of the stack variable"],
        type_name: Annotated[str, "Type of the stack variable"]
):
    """ For a given function, create a stack variable at an offset and with a specific type """

    func = idaapi.get_func(parse_address(function_address))
    if not func:
        raise IDAError(f"No function found at address {function_address}")

    ea = parse_address(offset)

    frame_tif = ida_typeinf.tinfo_t()
    if not ida_frame.get_func_frame(frame_tif, func):
        raise IDAError("No frame returned.")

    tif = get_type_by_name(type_name)
    if not ida_frame.define_stkvar(func, variable_name, ea, tif):
        raise IDAError("failed to define stack frame variable")

@jsonrpc
@idawrite
def set_stack_frame_variable_type(
        function_address: Annotated[str, "Address of the disassembled function to set the stack frame variables"],
        variable_name: Annotated[str, "Name of the stack variable"],
        type_name: Annotated[str, "Type of the stack variable"]
):
    """ For a given disassembled function, set the type of a stack variable """

    func = idaapi.get_func(parse_address(function_address))
    if not func:
        raise IDAError(f"No function found at address {function_address}")

    frame_tif = ida_typeinf.tinfo_t()
    if not ida_frame.get_func_frame(frame_tif, func):
        raise IDAError("No frame returned.")

    idx, udm = frame_tif.get_udm(variable_name)                                                  
    if not udm:
        raise IDAError(f"{variable_name} not found.")

    tid = frame_tif.get_udm_tid(idx)
    udm = ida_typeinf.udm_t()
    frame_tif.get_udm_by_tid(udm, tid)
    offset = udm.offset // 8

    tif = get_type_by_name(type_name)
    if not ida_frame.set_frame_member_type(func, offset, tif):
        raise IDAError("failed to set stack frame variable type")

@jsonrpc
@idawrite
def delete_stack_frame_variable(
        function_address: Annotated[str, "Address of the function to set the stack frame variables"],
        variable_name: Annotated[str, "Name of the stack variable"]
):
    """ Delete the named stack variable for a given function """

    func = idaapi.get_func(parse_address(function_address))
    if not func:
        raise IDAError(f"No function found at address {function_address}")

    frame_tif = ida_typeinf.tinfo_t()
    if not ida_frame.get_func_frame(frame_tif, func):
        raise IDAError("No frame returned.")

    idx, udm = frame_tif.get_udm(variable_name)                                                  
    if not udm:
        raise IDAError(f"{variable_name} not found.")

    tid = frame_tif.get_udm_tid(idx)
    if ida_frame.is_special_frame_member(tid):
        raise IDAError(f"{variable_name} is a special frame member. Will not delete.")

    udm = ida_typeinf.udm_t()
    frame_tif.get_udm_by_tid(udm, tid)
    offset = udm.offset // 8
    size = udm.size // 8
    if ida_frame.is_funcarg_off(func, offset):
        raise IDAError(f"{variable_name} is an argument member. Will not delete.")

    if not ida_frame.delete_frame_members(func, offset, offset+size):
        raise IDAError("failed to delete stack frame variable")

@jsonrpc
@idaread
def read_memory_bytes(
        memory_address: Annotated[str, "Address of the memory value to be read"],
        size: Annotated[int, "size of memory to read"]
) -> str:
    """
    Read bytes at a given address.

    Only use this function if `get_global_variable_at` and `get_global_variable_by_name`
    both failed.
    """
    return ' '.join(f'{x:#02x}' for x in ida_bytes.get_bytes(parse_address(memory_address), size))

@jsonrpc
@idaread
def data_read_byte(
    address: Annotated[str, "Address to get 1 byte value from"],
) -> int:
    """
    Read the 1 byte value at the specified address.

    Only use this function if `get_global_variable_at` failed.
    """
    ea = parse_address(address)
    return ida_bytes.get_wide_byte(ea)

@jsonrpc
@idaread
def data_read_word(
    address: Annotated[str, "Address to get 2 bytes value from"],
) -> int:
    """
    Read the 2 byte value at the specified address as a WORD.

    Only use this function if `get_global_variable_at` failed.
    """
    ea = parse_address(address)
    return ida_bytes.get_wide_word(ea)

@jsonrpc
@idaread
def data_read_dword(
    address: Annotated[str, "Address to get 4 bytes value from"],
) -> int:
    """
    Read the 4 byte value at the specified address as a DWORD.

    Only use this function if `get_global_variable_at` failed.
    """
    ea = parse_address(address)
    return ida_bytes.get_wide_dword(ea)

@jsonrpc
@idaread
def data_read_qword(
        address: Annotated[str, "Address to get 8 bytes value from"]
) -> int:
    """
    Read the 8 byte value at the specified address as a QWORD.

    Only use this function if `get_global_variable_at` failed.
    """
    ea = parse_address(address)
    return ida_bytes.get_qword(ea)

@jsonrpc
@idaread
def data_read_string(
        address: Annotated[str, "Address to get string from"]
) -> str:
    """
    Read the string at the specified address.

    Only use this function if `get_global_variable_at` failed.
    """
    try:
        return idaapi.get_strlit_contents(parse_address(address),-1,0).decode("utf-8")
    except Exception as e:
        return "Error:" + str(e)

class RegisterValue(TypedDict):
    name: str
    value: str

class ThreadRegisters(TypedDict):
    thread_id: int
    registers: list[RegisterValue]

def dbg_ensure_running() -> "ida_idd.debugger_t":
    dbg = ida_idd.get_dbg()
    if not dbg:
        raise IDAError("Debugger not running")
    return dbg

@jsonrpc
@idaread
@unsafe
def dbg_get_registers() -> list[ThreadRegisters]:
    """Get all registers and their values. This function is only available when debugging."""
    result: list[ThreadRegisters] = []
    dbg = dbg_ensure_running()
    for thread_index in range(ida_dbg.get_thread_qty()):
        tid = ida_dbg.getn_thread(thread_index)
        regs = []
        regvals = ida_dbg.get_reg_vals(tid)
        for reg_index, rv in enumerate(regvals):
            reg_info = dbg.regs(reg_index)
            reg_value = rv.pyval(reg_info.dtype)
            if isinstance(reg_value, int):
                reg_value = hex(reg_value)
            if isinstance(reg_value, bytes):
                reg_value = reg_value.hex(" ")
            else:
                reg_value = str(reg_value)
            regs.append({
                "name": reg_info.name,
                "value": reg_value,
            })
        result.append({
            "thread_id": tid,
            "registers": regs,
        })
    return result

@jsonrpc
@idaread
@unsafe
def dbg_get_call_stack() -> list[dict[str, str]]:
    """Get the current call stack."""
    callstack = []
    try:
        tid = ida_dbg.get_current_thread()
        trace = ida_idd.call_stack_t()

        if not ida_dbg.collect_stack_trace(tid, trace):
            return []
        for frame in trace:
            frame_info = {
                "address": hex(frame.callea),
            }
            try:
                module_info = ida_idd.modinfo_t()
                if ida_dbg.get_module_info(frame.callea, module_info):
                    frame_info["module"] = os.path.basename(module_info.name)
                else:
                    frame_info["module"] = "<unknown>"

                name = (
                    ida_name.get_nice_colored_name(
                        frame.callea,
                        ida_name.GNCN_NOCOLOR
                        | ida_name.GNCN_NOLABEL
                        | ida_name.GNCN_NOSEG
                        | ida_name.GNCN_PREFDBG,
                    )
                    or "<unnamed>"
                )
                frame_info["symbol"] = name

            except Exception as e:
                frame_info["module"] = "<error>"
                frame_info["symbol"] = str(e)

            callstack.append(frame_info)

    except Exception as e:
        pass
    return callstack

class Breakpoint(TypedDict):
    ea: str
    enabled: bool
    condition: Optional[str]

def list_breakpoints():
    breakpoints: list[Breakpoint] = []
    for i in range(ida_dbg.get_bpt_qty()):
        bpt = ida_dbg.bpt_t()
        if ida_dbg.getn_bpt(i, bpt):
            breakpoints.append(Breakpoint(
                ea=hex(bpt.ea),
                enabled=bpt.flags & ida_dbg.BPT_ENABLED,
                condition=str(bpt.condition) if bpt.condition else None,
            ))
    return breakpoints

@jsonrpc
@idaread
@unsafe
def dbg_list_breakpoints():
    """List all breakpoints in the program."""
    return list_breakpoints()

@jsonrpc
@idaread
@unsafe
def dbg_start_process():
    """Start the debugger, returns the current instruction pointer"""

    if len(list_breakpoints()) == 0:
        for i in range(ida_entry.get_entry_qty()):
            ordinal = ida_entry.get_entry_ordinal(i)
            address = ida_entry.get_entry(ordinal)
            if address != ida_idaapi.BADADDR:
                ida_dbg.add_bpt(address, 0, idaapi.BPT_SOFT)

    if idaapi.start_process("", "", "") == 1:
        ip = ida_dbg.get_ip_val()
        if ip is not None:
            return hex(ip)
    raise IDAError("Failed to start debugger (did the user configure the debugger manually one time?)")

@jsonrpc
@idaread
@unsafe
def dbg_exit_process():
    """Exit the debugger"""
    dbg_ensure_running()
    if idaapi.exit_process():
        return
    raise IDAError("Failed to exit debugger")

@jsonrpc
@idaread
@unsafe
def dbg_continue_process() -> str:
    """Continue the debugger, returns the current instruction pointer"""
    dbg_ensure_running()
    if idaapi.continue_process():
        ip = ida_dbg.get_ip_val()
        if ip is not None:
            return hex(ip)
    raise IDAError("Failed to continue debugger")

@jsonrpc
@idaread
@unsafe
def dbg_run_to(
    address: Annotated[str, "Run the debugger to the specified address"],
):
    """Run the debugger to the specified address"""
    dbg_ensure_running()
    ea = parse_address(address)
    if idaapi.run_to(ea):
        ip = ida_dbg.get_ip_val()
        if ip is not None:
            return hex(ip)
    raise IDAError(f"Failed to run to address {hex(ea)}")

@jsonrpc
@idaread
@unsafe
def dbg_set_breakpoint(
    address: Annotated[str, "Set a breakpoint at the specified address"],
):
    """Set a breakpoint at the specified address"""
    ea = parse_address(address)
    if idaapi.add_bpt(ea, 0, idaapi.BPT_SOFT):
        return f"Breakpoint set at {hex(ea)}"
    breakpoints = list_breakpoints()
    for bpt in breakpoints:
        if bpt["ea"] == hex(ea):
            return
    raise IDAError(f"Failed to set breakpoint at address {hex(ea)}")

@jsonrpc
@idaread
@unsafe
def dbg_step_into():
    """Step into the current instruction"""
    dbg_ensure_running()
    if idaapi.step_into():
        ip = ida_dbg.get_ip_val()
        if ip is not None:
            return hex(ip)
    raise IDAError("Failed to step into")

@jsonrpc
@idaread
@unsafe
def dbg_step_over():
    """Step over the current instruction"""
    dbg_ensure_running()
    if idaapi.step_over():
        ip = ida_dbg.get_ip_val()
        if ip is not None:
            return hex(ip)
    raise IDAError("Failed to step over")

@jsonrpc
@idaread
@unsafe
def dbg_delete_breakpoint(
    address: Annotated[str, "del a breakpoint at the specified address"],
):
    """Delete a breakpoint at the specified address"""
    ea = parse_address(address)
    if idaapi.del_bpt(ea):
        return
    raise IDAError(f"Failed to delete breakpoint at address {hex(ea)}")

@jsonrpc
@idaread
@unsafe
def dbg_enable_breakpoint(
    address: Annotated[str, "Enable or disable a breakpoint at the specified address"],
    enable: Annotated[bool, "Enable or disable a breakpoint"],
):
    """Enable or disable a breakpoint at the specified address"""
    ea = parse_address(address)
    if idaapi.enable_bpt(ea, enable):
        return
    raise IDAError(f"Failed to {'' if enable else 'disable '}breakpoint at address {hex(ea)}")

                                                                                
                                               
                                                        
                                                                                
import datetime
import hashlib
import json
import os
import sys
import time
import re
import site
import importlib
import threading
import urllib.request
import urllib.error

                                                                                 
try:
    user_site = site.getusersitepackages()
    if user_site and user_site not in sys.path:
        sys.path.insert(0, user_site)
    for sp in site.getsitepackages():
        if sp not in sys.path:
            sys.path.insert(0, sp)
except Exception:
    pass

try:
    import PyQt5
    if user_site:
        user_pyqt5 = os.path.join(user_site, "PyQt5")
        if os.path.isdir(user_pyqt5) and user_pyqt5 not in PyQt5.__path__:
            PyQt5.__path__.insert(0, user_pyqt5)
    
    import PyQt5.sip
    sys.modules['sip'] = PyQt5.sip
    sys.modules['PyQt5.sip'] = PyQt5.sip
    main_mod = sys.modules.get('__main__')
    if main_mod:
        setattr(main_mod, 'sip', PyQt5.sip)
except Exception:
    pass

QtWidgets = None
QtCore = None
QtGui = None

try:
    import PyQt5.QtWidgets as QtWidgets
    import PyQt5.QtCore as QtCore
    import PyQt5.QtGui as QtGui
    main_mod = sys.modules.get('__main__')
    if main_mod:
        setattr(main_mod, 'QtWidgets', QtWidgets)
        setattr(main_mod, 'QtCore', QtCore)
        setattr(main_mod, 'QtGui', QtGui)
except Exception as e:
    for qt_pkg in ["PySide6", "PySide2"]:
        try:
            QtWidgets = importlib.import_module(f"{qt_pkg}.QtWidgets")
            QtCore = importlib.import_module(f"{qt_pkg}.QtCore")
            QtGui = importlib.import_module(f"{qt_pkg}.QtGui")
            if QtWidgets:
                break
        except Exception:
            continue

                                                                                
                                       
                                                                                
MCP_TOOLS_CATALOG = [
                           
    {"name": "get_metadata", "category": "Navigation", "desc": "Returns binary module name, base address, sha256, processor type"},
    {"name": "get_current_address", "category": "Navigation", "desc": "Returns currently focused screen cursor address"},
    {"name": "get_current_function", "category": "Navigation", "desc": "Returns full details of function at current screen cursor"},
    {"name": "get_function_by_address", "category": "Navigation", "desc": "Lookup function metadata by starting or interior address"},
    {"name": "get_function_by_name", "category": "Navigation", "desc": "Lookup function metadata by symbol/mangled name"},
    {"name": "list_functions", "category": "Navigation", "desc": "Enumerate functions defined in the IDA database"},
    {"name": "list_globals", "category": "Navigation", "desc": "List global variables and data symbols"},
    {"name": "list_globals_filter", "category": "Navigation", "desc": "Filter global variables matching pattern"},
    {"name": "list_imports", "category": "Navigation", "desc": "Enumerate imported modules and functions"},
    {"name": "list_strings", "category": "Navigation", "desc": "Enumerate extracted ASCII/Unicode strings"},
    {"name": "list_strings_filter", "category": "Navigation", "desc": "Filter string literals by search query"},
    {"name": "list_local_types", "category": "Navigation", "desc": "List local types, structs, and typedefs"},
    {"name": "convert_number", "category": "Navigation", "desc": "Convert integer between hex, decimal, and binary"},

                                 
    {"name": "decompile_function", "category": "Decompilation", "desc": "Run Hex-Rays decompiler and generate high-level C pseudocode"},
    {"name": "disassemble_function", "category": "Disassembly", "desc": "Generate detailed assembly instruction listing"},

                              
    {"name": "get_xrefs_to", "category": "Xrefs", "desc": "Query all code and data cross-references pointing to address"},
    {"name": "get_xrefs_to_field", "category": "Xrefs", "desc": "Query cross-references to a specific structure member"},
    {"name": "get_callees", "category": "Calls", "desc": "Enumerate all subroutines called by the target function"},
    {"name": "get_callers", "category": "Calls", "desc": "Enumerate all functions that call the target function"},
    {"name": "get_entry_points", "category": "Navigation", "desc": "List binary entry points and exported routines"},

                         
    {"name": "set_comment", "category": "Modification", "desc": "Add or update regular/repeatable comment at address"},
    {"name": "rename_local_variable", "category": "Modification", "desc": "Rename variable in Hex-Rays decompiled pseudocode"},
    {"name": "rename_global_variable", "category": "Modification", "desc": "Rename global symbol or variable in IDA database"},
    {"name": "set_global_variable_type", "category": "Modification", "desc": "Apply C type declaration to global variable"},
    {"name": "rename_function", "category": "Modification", "desc": "Rename function symbol in database"},
    {"name": "set_function_prototype", "category": "Modification", "desc": "Set function signature and prototype definition"},
    {"name": "declare_c_type", "category": "Modification", "desc": "Declare new C struct/enum/type in IDA database"},
    {"name": "set_local_variable_type", "category": "Modification", "desc": "Change local variable type in Hex-Rays view"},
    {"name": "patch_address_assembles", "category": "Modification", "desc": "Assemble instruction and patch bytes into database"},

                               
    {"name": "get_stack_frame_variables", "category": "Stack", "desc": "List local stack frame variables and offsets"},
    {"name": "rename_stack_frame_variable", "category": "Stack", "desc": "Rename stack frame variable"},
    {"name": "create_stack_frame_variable", "category": "Stack", "desc": "Define new stack variable in function frame"},
    {"name": "set_stack_frame_variable_type", "category": "Stack", "desc": "Set type for stack frame variable"},
    {"name": "delete_stack_frame_variable", "category": "Stack", "desc": "Remove stack frame variable"},
    {"name": "get_defined_structures", "category": "Structures", "desc": "Enumerate all declared structures in IDB"},
    {"name": "analyze_struct_detailed", "category": "Structures", "desc": "Detailed field offsets and member types of structure"},
    {"name": "get_struct_at_address", "category": "Structures", "desc": "Map structure layout onto target memory address"},
    {"name": "get_struct_info_simple", "category": "Structures", "desc": "Get struct name, total size, and member count"},
    {"name": "search_structures", "category": "Structures", "desc": "Search defined structures by name substring"},

                          
    {"name": "read_memory_bytes", "category": "Memory", "desc": "Read arbitrary byte buffer from memory"},
    {"name": "data_read_byte", "category": "Memory", "desc": "Read 8-bit byte at address"},
    {"name": "data_read_word", "category": "Memory", "desc": "Read 16-bit word at address"},
    {"name": "data_read_dword", "category": "Memory", "desc": "Read 32-bit dword at address"},
    {"name": "data_read_qword", "category": "Memory", "desc": "Read 64-bit qword pointer at address"},
    {"name": "data_read_string", "category": "Memory", "desc": "Read null-terminated ASCII string at address"},
    {"name": "get_global_variable_value_by_name", "category": "Memory", "desc": "Read value of global variable by symbol"},
    {"name": "get_global_variable_value_at_address", "category": "Memory", "desc": "Read value of global variable by address"},

                           
    {"name": "dbg_get_registers", "category": "Debugger", "desc": "Read CPU register context of active thread"},
    {"name": "dbg_get_call_stack", "category": "Debugger", "desc": "Retrieve call stack backtrace frames"},
    {"name": "dbg_list_breakpoints", "category": "Debugger", "desc": "Enumerate active breakpoints and hit counts"},
    {"name": "dbg_start_process", "category": "Debugger", "desc": "Launch process under IDA debugger"},
    {"name": "dbg_exit_process", "category": "Debugger", "desc": "Terminate debugged process"},
    {"name": "dbg_continue_process", "category": "Debugger", "desc": "Resume execution of debugged process"},
    {"name": "dbg_run_to", "category": "Debugger", "desc": "Execute debugged process until address is reached"},
    {"name": "dbg_set_breakpoint", "category": "Debugger", "desc": "Set software/hardware breakpoint at address"},
    {"name": "dbg_step_into", "category": "Debugger", "desc": "Step into subroutine call"},
    {"name": "dbg_step_over", "category": "Debugger", "desc": "Step over subroutine call"},
    {"name": "dbg_delete_breakpoint", "category": "Debugger", "desc": "Delete breakpoint at address"},
    {"name": "dbg_enable_breakpoint", "category": "Debugger", "desc": "Enable or disable breakpoint"}
]

                                                                                
                                     
                                                                                

DASHBOARD_I18N = {
    "en": {
        "title": "MCP DASHBOARD",
        "funcs_suffix": "Funcs",
        "agent_prefix": "Agent:",
        "mcp_online": "MCP Online :13337",
        "mcp_offline": "MCP Offline",
        "refresh": "Refresh",
        "refreshing": "Refreshing...",
        "refresh_tooltip": "Reload binary state, cursor info, and server telemetry",
        "tab_mcp": "MCP Server && Agents",
        "tab_copilot": "AI Copilot && Deobfuscation",
        "tab_decompiled": "Decompiled C",
        "tab_xrefs": "Calls && Xrefs",
        "tab_log": "Activity Log",
        "server_status_title": "Model Context Protocol (MCP) Server Status",
        "copy_mcp_url": "Copy MCP URL",
        "test_connection": "Test Connection",
        "testing_connection": "Testing...",
        "url_copied": "Endpoint URL copied to clipboard: http://127.0.0.1:13337/mcp",
        "proto_badge": "Protocol: JSON-RPC 2.0",
        "tools_badge": "Tools: 59 Methods",
        "reqs_badge": "Requests: {count} Processed",
        "user_agent_cfg_title": "User Agent Configuration",
        "user_agent_cfg_sub": "(Integrated with AI Pair Programmer)",
        "select_agent_lbl": "Select Your AI Agent:",
        "copy_agent_cfg": "Copy Agent Config (JSON)",
        "save_agent_pref": "Save Agent Preference",
        "run_local_agent": "Run ida_mcp_agent",
        "agent_pref_saved": "Agent preference saved successfully!",
        "agent_cfg_copied": "JSON configuration copied to clipboard!",
        "tools_catalog_title": "MCP Registered Tools Catalog",
        "tools_registered_count": "({count} Methods Registered)",
        "tools_showing_count": "(Showing {shown} of {total} Methods)",
        "category_lbl": "Category:",
        "all_categories": "All Categories",
        "tools_filter_placeholder": "Filter tools (e.g. decompile, dbg, struct, xref)...",
        "col_no": "No",
        "col_method_name": "Method Name",
        "col_category": "Category",
        "col_calls": "Calls",
        "col_desc": "Description",
        "func_title_loading": "Function: Loading...",
        "func_title": "Function: {name}",
        "cff_badge": "CFF OBFUSCATED",
        "jump_in_ida": "Jump in IDA",
        "target_func_lbl": "Target Function:",
        "func_range": "Range: {start} - {end} ({size} bytes)",
        "btn_deobfuscate": "Deobfuscate && Explain",
        "btn_suggest_renames": "Suggest Renames",
        "btn_apply_renames": "Apply Renames to IDA",
        "btn_send_to_agent": "Send to {agent}",
        "ai_placeholder": "Agent analysis, deobfuscated pseudocode summary, and reverse engineering reasoning will appear here...",
        "ask_placeholder": "Ask {agent}: e.g. 'How to bypass this check?', 'Explain the CFF logic'...",
        "ask_btn": "Ask {agent}",
        "copy_c_code": "Copy C Code",
        "save_c_file": "Save to ida_current_function.c",
        "c_copied": "Pseudocode copied to clipboard!",
        "c_saved": "Saved & Exported '{name}'!",
        "callers_header": "Callers ({count} functions calling this):",
        "callees_header": "Callees ({count} subroutines called):",
        "log_stream_title": "Live MCP RPC Stream: Connected to {agent}",
        "filter_event_lbl": "Filter Event:",
        "all_events": "All Events",
        "agent_calls": "Agent Calls",
        "agent_success": "Agent Success",
        "errors": "Errors",
        "ui_actions": "UI Actions",
        "clear_log": "Clear Log",
        "custom_agent_title": "Custom AI Agent & Model Provider Configuration",
        "direct_llm_mode": "Direct LLM Mode",
        "provider_name_lbl": "Provider Name:",
        "base_url_lbl": "Base URL:",
        "api_key_lbl": "API Key:",
        "api_key_placeholder": "Enter API Key (saved encrypted)",
        "show_key": "Show",
        "hide_key": "Hide",
        "model_id_lbl": "Model ID:",
        "model_combo_tooltip": "Dynamic model list from API provider or manual input as fallback",
        "models_count": "({count} models)",
        "fetch_models": "Fetch Models",
        "refresh_models": "Refresh Models",
        "fetching_models": "Fetching...",
        "test_selected_model": "Test Selected Model",
        "testing_model": "Testing Model...",
        "save_agent_config": "Save Agent Config",
        "fetch_status_initial": "Status: Not fetched yet (click 'Fetch Models' or enter Base URL & API Key)",
        "fetch_status_fetching": "Fetching model list from provider...",
        "fetch_status_success": "Fetch succeeded: {count} models loaded at {time}",
        "fetch_status_empty": "Empty state: Provider returned empty list at {time}",
        "fetch_status_failed": "Failed to fetch models ({time}): {msg}",
        "testing_endpoint": "Testing MCP endpoint at http://127.0.0.1:13337/mcp...",
        "conn_success": "SUCCESS: {msg}",
        "conn_failed": "FAILED: {msg}",
        "jumped_to": "Jumped in IDA to {name}",
        "no_func_selected": "No active function selected.",
        "renamed_success": "Successfully renamed {count} variables in Hex-Rays view!",
        "no_renames": "No renames to apply",
        "click_apply_renames": "Click <b>'Apply Renames to IDA'</b> to apply proposed variable renames directly in Hex-Rays pseudocode.",
        "lang_selector_lbl": "Language:",
        "cat_nav": "Navigation",
        "cat_calls": "Calls",
        "cat_mod": "Modification",
        "cat_stack": "Stack",
        "cat_struct": "Structures",
        "cat_mem": "Memory",
        "cat_dbg": "Debugger"
    },
    "id": {
        "title": "DASHBOARD MCP",
        "funcs_suffix": "Fungsi",
        "agent_prefix": "Agen:",
        "mcp_online": "MCP Aktif :13337",
        "mcp_offline": "MCP Tidak Aktif",
        "refresh": "Segarkan",
        "refreshing": "Menyegarkan...",
        "refresh_tooltip": "Muat ulang status biner, posisi kursor, dan telemetri server",
        "tab_mcp": "Server MCP && Agen",
        "tab_copilot": "AI Copilot && Deobfuskasi",
        "tab_decompiled": "Hasil Dekompilasi C",
        "tab_xrefs": "Panggilan && Xrefs",
        "tab_log": "Log Aktivitas",
        "server_status_title": "Status Server Model Context Protocol (MCP)",
        "copy_mcp_url": "Salin URL MCP",
        "test_connection": "Uji Koneksi",
        "testing_connection": "Menguji...",
        "url_copied": "URL endpoint disalin ke papan klip: http://127.0.0.1:13337/mcp",
        "proto_badge": "Protokol: JSON-RPC 2.0",
        "tools_badge": "Peralatan: 59 Metode",
        "reqs_badge": "Permintaan: {count} Diproses",
        "user_agent_cfg_title": "Konfigurasi Agen Pengguna",
        "user_agent_cfg_sub": "(Terintegrasi dengan AI Pair Programmer)",
        "select_agent_lbl": "Pilih Agen AI Anda:",
        "copy_agent_cfg": "Salin Konfigurasi Agen (JSON)",
        "save_agent_pref": "Simpan Preferensi Agen",
        "run_local_agent": "Jalankan ida_mcp_agent",
        "agent_pref_saved": "Preferensi agen berhasil disimpan!",
        "agent_cfg_copied": "Konfigurasi JSON disalin ke papan klip!",
        "tools_catalog_title": "Katalog Peralatan Terdaftar MCP",
        "tools_registered_count": "({count} Metode Terdaftar)",
        "tools_showing_count": "(Menampilkan {shown} dari {total} Metode)",
        "category_lbl": "Kategori:",
        "all_categories": "Semua Kategori",
        "tools_filter_placeholder": "Saring alat (misal: decompile, dbg, struct, xref)...",
        "col_no": "No",
        "col_method_name": "Nama Metode",
        "col_category": "Kategori",
        "col_calls": "Panggilan",
        "col_desc": "Deskripsi",
        "func_title_loading": "Fungsi: Memuat...",
        "func_title": "Fungsi: {name}",
        "cff_badge": "TEROBFUSKASI CFF",
        "jump_in_ida": "Lompat di IDA",
        "target_func_lbl": "Fungsi Target:",
        "func_range": "Rentang: {start} - {end} ({size} byte)",
        "btn_deobfuscate": "Deobfuskasi && Jelaskan",
        "btn_suggest_renames": "Saran Ubah Nama",
        "btn_apply_renames": "Terapkan Nama ke IDA",
        "btn_send_to_agent": "Kirim ke {agent}",
        "ai_placeholder": "Analisis agen, ringkasan kode semu hasil deobfuskasi, dan penalaran reverse engineering akan tampil di sini...",
        "ask_placeholder": "Tanya {agent}: misal 'Bagaimana cara bypass pemeriksaan ini?', 'Jelaskan logika CFF'...",
        "ask_btn": "Tanya {agent}",
        "copy_c_code": "Salin Kode C",
        "save_c_file": "Simpan ke ida_current_function.c",
        "c_copied": "Kode semu berhasil disalin ke papan klip!",
        "c_saved": "Berhasil menyimpan dan mengekspor '{name}'!",
        "callers_header": "Pemanggil ({count} fungsi memanggil ini):",
        "callees_header": "Dipanggil ({count} subrutin dipanggil):",
        "log_stream_title": "Aliran Langsung RPC MCP: Terhubung ke {agent}",
        "filter_event_lbl": "Saring Peristiwa:",
        "all_events": "Semua Peristiwa",
        "agent_calls": "Panggilan Agen",
        "agent_success": "Agen Berhasil",
        "errors": "Kesalahan",
        "ui_actions": "Aksi Antarmuka",
        "clear_log": "Bersihkan Log",
        "custom_agent_title": "Konfigurasi Agen AI Kustom & Penyedia Model",
        "direct_llm_mode": "Mode LLM Langsung",
        "provider_name_lbl": "Nama Penyedia:",
        "base_url_lbl": "URL Basis:",
        "api_key_lbl": "Kunci API:",
        "api_key_placeholder": "Masukkan Kunci API (tersimpan terenkripsi)",
        "show_key": "Tampilkan",
        "hide_key": "Sembunyikan",
        "model_id_lbl": "ID Model:",
        "model_combo_tooltip": "Daftar model dinamis dari penyedia API atau input manual",
        "models_count": "({count} model)",
        "fetch_models": "Ambil Model",
        "refresh_models": "Segarkan Model",
        "fetching_models": "Mengambil...",
        "test_selected_model": "Uji Model Terpilih",
        "testing_model": "Menguji Model...",
        "save_agent_config": "Simpan Konfigurasi Agen",
        "fetch_status_initial": "Status: Belum diambil (klik 'Ambil Model' atau isi URL Basis & Kunci API)",
        "fetch_status_fetching": "Mengambil daftar model dari penyedia...",
        "fetch_status_success": "Berhasil mengambil: {count} model dimuat pada {time}",
        "fetch_status_empty": "Daftar kosong: Penyedia mengembalikan daftar kosong pada {time}",
        "fetch_status_failed": "Gagal mengambil model ({time}): {msg}",
        "testing_endpoint": "Menguji endpoint MCP di http://127.0.0.1:13337/mcp...",
        "conn_success": "BERHASIL: {msg}",
        "conn_failed": "GAGAL: {msg}",
        "jumped_to": "Melompat di IDA ke {name}",
        "no_func_selected": "Tidak ada fungsi aktif yang dipilih.",
        "renamed_success": "Berhasil mengubah nama {count} variabel pada tampilan Hex-Rays!",
        "no_renames": "Tidak ada nama variabel yang perlu diubah",
        "click_apply_renames": "Klik <b>'Terapkan Nama ke IDA'</b> untuk menerapkan saran nama variabel langsung ke kode semu Hex-Rays.",
        "lang_selector_lbl": "Bahasa:",
        "cat_nav": "Navigasi",
        "cat_calls": "Panggilan",
        "cat_mod": "Modifikasi",
        "cat_stack": "Stack",
        "cat_struct": "Struktur",
        "cat_mem": "Memori",
        "cat_dbg": "Debugger"
    }
}

class McpDashboardState:
    server = None
    tab_instance = None
    ui_hooks = None
    idb_hooks = None
    last_cursor_ea = None
    current_selected_func = None
    event_history = []
    last_rename_suggestions = []
    total_rpc_requests = 0
    rpc_stats = {}
    current_language = "en"

    session_file_path = r"c:\Users\user\Documents\project\ida_active_session.json"
    func_export_file = r"c:\Users\user\Documents\project\ida_current_function.c"
    context_export_file = r"c:\Users\user\Documents\project\ida_agent_context.json"
    chat_file_path = r"c:\Users\user\Documents\project\ida_agent_chat.json"
    config_file_path = r"c:\Users\user\Documents\project\ida_agent_config.json"

    agent_config = {
        "active_agent": "Antigravity",
        "language": "en",
        "agents": {
            "Antigravity": {
                "name": "Antigravity Agent",
                "tag": "Google DeepMind AGY",
                "type": "external_mcp",
                "desc": "Autonomous Pair-Programming & RE Agent connecting via ida-mcp.json",
                "config_file": "c:\\Users\\user\\Documents\\project\\ida-mcp.json",
                "instructions": "Running with Antigravity. Prompts and context are synced automatically to project files."
            },
            "Claude": {
                "name": "Claude Desktop / Code",
                "tag": "Anthropic Claude 3.7",
                "type": "external_mcp",
                "desc": "Anthropic Claude connected via claude_desktop_config.json",
                "config_path": "%APPDATA%\\Claude\\claude_desktop_config.json",
                "instructions": "Add the MCP server block to claude_desktop_config.json, then restart Claude."
            },
            "Cursor": {
                "name": "Cursor / Windsurf",
                "tag": "Cursor IDE",
                "type": "external_mcp",
                "desc": "AI Editor with built-in MCP Client support",
                "config_path": ".cursor/mcp.json",
                "instructions": "Add to .cursor/mcp.json in your workspace root."
            },
            "Direct_API": {
                "name": "Direct AI API",
                "tag": "In-IDA Direct LLM",
                "type": "direct_llm",
                "provider": "Gemini",
                "model": "gemini-2.0-flash",
                "api_key": "",
                "base_url": "https://generativelanguage.googleapis.com/v1beta",
                "ollama_url": "http://localhost:11434/v1",
                "desc": "Direct query inside IDA Pro using Gemini, OpenAI, Claude, DeepSeek, or Ollama"
            },
            "Local_Script": {
                "name": "ida_mcp_agent (Python)",
                "tag": "Autonomous Script",
                "type": "local_script",
                "script_path": r"c:\Users\user\Documents\project\ida_mcp_agent\agent_runner.py",
                "desc": "Local autonomous pointer resolver & decompilation agent"
            }
        }
    }

    _SECRET_SALT = "ida_pro_mcp_salt_secure_2026"
    _models_cache = {}

    @classmethod
    def get_language(cls) -> str:
        if hasattr(cls, "current_language") and cls.current_language in ("en", "id"):
            return cls.current_language
        return "en"

    @classmethod
    def set_language(cls, lang: str):
        if lang in ("en", "id"):
            cls.current_language = lang
            cls.agent_config["language"] = lang
            cls.save_agent_config()
            if cls.tab_instance and hasattr(cls.tab_instance, "apply_language"):
                cls.tab_instance.apply_language(lang)

    @classmethod
    def t(cls, key: str, **kwargs) -> str:
        lang = cls.get_language()
        dict_lang = DASHBOARD_I18N.get(lang, DASHBOARD_I18N["en"])
        val = dict_lang.get(key, DASHBOARD_I18N["en"].get(key, key))
        if kwargs:
            try:
                return val.format(**kwargs)
            except Exception:
                return val
        return val

    @classmethod
    def encode_secret(cls, raw: str) -> str:
        if not raw:
            return ""
        salt_bytes = cls._SECRET_SALT.encode("utf-8")
        raw_bytes = raw.encode("utf-8")
        xored = bytes(b ^ salt_bytes[i % len(salt_bytes)] for i, b in enumerate(raw_bytes))
        return "enc_" + base64.b64encode(xored).decode("ascii")

    @classmethod
    def decode_secret(cls, enc: str) -> str:
        if not enc:
            return ""
        if not enc.startswith("enc_"):
            return enc
        try:
            data = base64.b64decode(enc[4:].encode("ascii"))
            salt_bytes = cls._SECRET_SALT.encode("utf-8")
            raw_bytes = bytes(b ^ salt_bytes[i % len(salt_bytes)] for i, b in enumerate(data))
            return raw_bytes.decode("utf-8", errors="replace")
        except Exception:
            return ""

    @classmethod
    def normalize_base_url(cls, base_url: str, provider: str = "") -> str:
        url = base_url.strip().rstrip("/")
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        return url

    @classmethod
    def build_models_url(cls, base_url: str, provider: str = "", api_key: str = "") -> str:
        norm = cls.normalize_base_url(base_url, provider)
        if "generativelanguage.googleapis.com" in norm or provider.lower() == "gemini":
            murl = norm if norm.endswith("/models") else f"{norm}/models"
            if api_key and "?" not in murl:
                murl = f"{murl}?key={api_key}"
            return murl
        if norm.endswith("/models"):
            return norm
        if norm.endswith("/v1"):
            return f"{norm}/models"
        return f"{norm}/v1/models"

    @classmethod
    def parse_models_response(cls, data: Any, provider: str = "") -> list:
        models = []
        seen = set()
        raw_list = []
        if isinstance(data, dict):
            if "data" in data and isinstance(data["data"], list):
                raw_list = data["data"]
            elif "models" in data and isinstance(data["models"], list):
                raw_list = data["models"]
            elif "result" in data and isinstance(data["result"], list):
                raw_list = data["result"]
        elif isinstance(data, list):
            raw_list = data

        for item in raw_list:
            mid = ""
            meta = {}
            if isinstance(item, str):
                mid = item.strip()
            elif isinstance(item, dict):
                mid = str(item.get("id") or item.get("name") or item.get("model") or "").strip()
                if provider.lower() == "gemini" and mid.startswith("models/"):
                    mid = mid[7:]
                for k in ("owned_by", "created", "context_window", "displayName", "description"):
                    if k in item and item[k] is not None:
                        meta[k] = item[k]
            if mid and mid not in seen:
                seen.add(mid)
                models.append({"id": mid, "meta": meta})
        models.sort(key=lambda m: m["id"].lower())
        return models

    @classmethod
    def _format_http_error(cls, err: urllib.error.HTTPError, api_key: str = "") -> str:
        code = err.code
        detail = ""
        try:
            raw = err.read().decode("utf-8", errors="replace")
            if api_key and len(api_key) > 4:
                raw = raw.replace(api_key, "sk-***")
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "error" in parsed:
                err_obj = parsed["error"]
                if isinstance(err_obj, dict):
                    detail = err_obj.get("message", "")
                elif isinstance(err_obj, str):
                    detail = err_obj
        except Exception:
            pass

        lang = cls.get_language()
        if lang == "id":
            if code == 400:
                return f"HTTP 400 Bad Request: Parameter request tidak valid. {detail}"
            elif code in (401, 403):
                return f"HTTP {code} Unauthorized: Kunci API salah atau hak akses tidak memadai. {detail}"
            elif code == 402:
                return f"HTTP 402 Payment Required: Saldo kredit atau kuota token tidak mencukupi. {detail}"
            elif code == 404:
                return f"HTTP 404 Not Found: Endpoint /models atau model tidak ditemukan. {detail}"
            elif code == 429:
                return f"HTTP 429 Rate Limit: Batas kuota atau kecepatan permintaan terlampaui. {detail}"
            elif code >= 500:
                return f"HTTP {code} Server Error: Gangguan pada server penyedia model. {detail}"
            return f"HTTP {code} Error: {detail if detail else err.reason}"
        else:
            if code == 400:
                return f"HTTP 400 Bad Request: Invalid request parameters. {detail}"
            elif code in (401, 403):
                return f"HTTP {code} Unauthorized: Invalid API key or insufficient permissions. {detail}"
            elif code == 402:
                return f"HTTP 402 Payment Required: Insufficient credit balance or token quota. {detail}"
            elif code == 404:
                return f"HTTP 404 Not Found: Endpoint /models or model ID not found. {detail}"
            elif code == 429:
                return f"HTTP 429 Rate Limit: Request limit or token quota exceeded. {detail}"
            elif code >= 500:
                return f"HTTP {code} Server Error: Provider service disruption. {detail}"
            return f"HTTP {code} Error: {detail if detail else err.reason}"

    @classmethod
    def fetch_provider_models(cls, provider: str, base_url: str, api_key: str, force_refresh: bool = False):
        norm_url = cls.normalize_base_url(base_url, provider)
        cache_key = f"{provider}:{norm_url}"
        now = time.time()
        lang = cls.get_language()
        if not force_refresh and cache_key in cls._models_cache:
            cached = cls._models_cache[cache_key]
            if now - cached.get("timestamp", 0) < 3600:
                msg = f"Memuat {len(cached.get('models', []))} model dari cache lokal" if lang == "id" else f"Loaded {len(cached.get('models', []))} models from local cache"
                return True, cached.get("models", []), msg

        models_url = cls.build_models_url(norm_url, provider)
        headers = {"Content-Type": "application/json", "User-Agent": "IDAPRO-MCP-Client/2.0"}
        if provider.lower() == "gemini":
            if api_key and "?" not in models_url:
                models_url = f"{models_url}?key={api_key}"
        else:
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

        try:
            req = urllib.request.Request(models_url, headers=headers)
            with urllib.request.urlopen(req, timeout=9) as resp:
                raw_bytes = resp.read()
                data = json.loads(raw_bytes.decode("utf-8"))
                parsed = cls.parse_models_response(data, provider)
                if not parsed:
                    msg = "Endpoint /models berhasil diakses namun tidak menemukan model (daftar kosong)" if lang == "id" else "Endpoint /models reached but no valid models returned (empty list)"
                    return False, [], msg
                cls._models_cache[cache_key] = {"timestamp": now, "models": parsed}
                msg = f"Berhasil memuat {len(parsed)} model dari penyedia" if lang == "id" else f"Successfully loaded {len(parsed)} models from provider"
                return True, parsed, msg
        except urllib.error.HTTPError as e:
            if e.code == 404 and not norm_url.endswith("/v1") and "/models" in models_url:
                alt_url = f"{norm_url}/models" if "/v1/models" in models_url else f"{norm_url}/v1/models"
                try:
                    alt_req = urllib.request.Request(alt_url, headers=headers)
                    with urllib.request.urlopen(alt_req, timeout=9) as resp:
                        raw_bytes = resp.read()
                        data = json.loads(raw_bytes.decode("utf-8"))
                        parsed = cls.parse_models_response(data, provider)
                        if parsed:
                            cls._models_cache[cache_key] = {"timestamp": now, "models": parsed}
                            msg = f"Berhasil memuat {len(parsed)} model dari penyedia (alt endpoint)" if lang == "id" else f"Loaded {len(parsed)} models from provider (alt endpoint)"
                            return True, parsed, msg
                except Exception:
                    pass
            msg = cls._format_http_error(e, api_key)
            return False, [], msg
        except urllib.error.URLError as e:
            msg = f"Koneksi gagal atau DNS timeout: {e.reason}" if lang == "id" else f"Connection failed or DNS timeout: {e.reason}"
            return False, [], msg
        except (json.JSONDecodeError, ValueError) as e:
            msg = f"Respons JSON malformed dari endpoint /models: {e}" if lang == "id" else f"Malformed JSON response from /models: {e}"
            return False, [], msg
        except Exception as e:
            clean_err = str(e).replace(api_key, "sk-***") if api_key else str(e)
            msg = f"Gagal mengambil model: {clean_err}" if lang == "id" else f"Failed to fetch models: {clean_err}"
            return False, [], msg

    @classmethod
    def test_provider_connection(cls, provider: str, base_url: str, api_key: str):
        t0 = time.time()
        ok, models, msg = cls.fetch_provider_models(provider, base_url, api_key, force_refresh=True)
        dt = int((time.time() - t0) * 1000)
        lang = cls.get_language()
        if ok:
            return True, f"Connection OK (HTTP 200 in {dt} ms) - Provider API online" if lang != "id" else f"Koneksi Berhasil (HTTP 200 dalam {dt} ms) - API Penyedia Terhubung"
        return False, f"Connection Test Failed ({dt} ms): {msg}" if lang != "id" else f"Uji Koneksi Gagal ({dt} ms): {msg}"

    @classmethod
    def test_selected_model_inference(cls, provider: str, base_url: str, api_key: str, model_id: str):
        lang = cls.get_language()
        if not model_id:
            return False, "Model ID belum dipilih" if lang == "id" else "No Model ID selected"
        norm_url = cls.normalize_base_url(base_url, provider)
        t0 = time.time()
        try:
            if provider.lower() == "gemini":
                if not api_key:
                    return False, "Kunci API wajib diisi untuk menguji model Gemini" if lang == "id" else "API Key is required to test Gemini model"
                url = f"{norm_url}/models/{model_id}:generateContent?key={api_key}"
                payload = {"contents": [{"parts": [{"text": "Reply OK"}]}], "generationConfig": {"maxOutputTokens": 5}}
                req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    dt = int((time.time() - t0) * 1000)
                    if "candidates" in data:
                        return True, f"Model '{model_id}' terverifikasi siap inferensi ({dt} ms)" if lang == "id" else f"Model '{model_id}' verified ready for inference ({dt} ms)"
                    return False, f"Unexpected response from Gemini: {list(data.keys())}"
            else:
                chat_url = f"{norm_url}/chat/completions" if not norm_url.endswith("/chat/completions") else norm_url
                headers = {"Content-Type": "application/json", "User-Agent": "IDAPRO-MCP-Client/2.0"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                payload = {
                    "model": model_id,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 5,
                    "temperature": 0.0
                }
                req = urllib.request.Request(chat_url, data=json.dumps(payload).encode("utf-8"), headers=headers)
                with urllib.request.urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    dt = int((time.time() - t0) * 1000)
                    if "choices" in data and len(data["choices"]) > 0:
                        return True, f"Model '{model_id}' terverifikasi siap inferensi ({dt} ms)" if lang == "id" else f"Model '{model_id}' verified ready for inference ({dt} ms)"
                    return False, "Provider returned HTTP 200 but response lacks choices"
        except urllib.error.HTTPError as e:
            msg = cls._format_http_error(e, api_key)
            return False, f"Inference Test Gagal: {msg}" if lang == "id" else f"Inference Test Failed: {msg}"
        except urllib.error.URLError as e:
            return False, f"Inference Test Gagal: Gangguan jaringan ({e.reason})" if lang == "id" else f"Inference Test Failed: Network error ({e.reason})"
        except Exception as e:
            clean_err = str(e).replace(api_key, "sk-***") if api_key else str(e)
            return False, f"Inference Test Gagal: {clean_err}" if lang == "id" else f"Inference Test Failed: {clean_err}"

    @classmethod
    def save_custom_agent_config(cls, provider: str, base_url: str, api_key: str, model: str):
        direct_cfg = cls.agent_config.setdefault("agents", {}).setdefault("Direct_API", {})
        old_url = direct_cfg.get("base_url", "")
        norm_new_url = cls.normalize_base_url(base_url, provider)
        if old_url and old_url.rstrip("/") != norm_new_url.rstrip("/"):
            old_key = f"{provider}:{old_url.rstrip('/')}"
            cls._models_cache.pop(old_key, None)
        direct_cfg["provider"] = provider
        direct_cfg["base_url"] = norm_new_url
        direct_cfg["model"] = model
        if api_key:
            direct_cfg["api_key_enc"] = cls.encode_secret(api_key)
            direct_cfg["api_key_masked"] = api_key[:3] + "***" + api_key[-3:] if len(api_key) >= 7 else "***"
            if "api_key" in direct_cfg:
                del direct_cfg["api_key"]
        cls.save_agent_config()
        cls.log_event("ui_action", f"AI Agent configuration saved: {provider} | Model: {model}")

    @classmethod
    def load_agent_config(cls):
        try:
            if os.path.isfile(cls.config_file_path):
                with open(cls.config_file_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if "language" in saved and saved["language"] in ("en", "id"):
                        cls.current_language = saved["language"]
                        cls.agent_config["language"] = saved["language"]
                    if "active_agent" in saved:
                        cls.agent_config["active_agent"] = saved["active_agent"]
                    if "agents" in saved:
                        for k, v in saved["agents"].items():
                            if k in cls.agent_config["agents"]:
                                cls.agent_config["agents"][k].update(v)
                            else:
                                cls.agent_config["agents"][k] = v
                direct_cfg = cls.agent_config.get("agents", {}).get("Direct_API", {})
                if "api_key" in direct_cfg and direct_cfg["api_key"]:
                    direct_cfg["api_key_enc"] = cls.encode_secret(direct_cfg["api_key"])
                    raw_k = direct_cfg["api_key"]
                    direct_cfg["api_key_masked"] = raw_k[:3] + "***" + raw_k[-3:] if len(raw_k) >= 7 else "***"
                    del direct_cfg["api_key"]
                    cls.save_agent_config()
        except Exception:
            pass

    @classmethod
    def save_agent_config(cls):
        try:
            os.makedirs(os.path.dirname(cls.config_file_path), exist_ok=True)
            cls.agent_config["language"] = cls.get_language()
            with open(cls.config_file_path, "w", encoding="utf-8") as f:
                json.dump(cls.agent_config, f, indent=2)
        except Exception:
            pass

    @classmethod
    def get_active_agent_key(cls):
        return cls.agent_config.get("active_agent", "Antigravity")

    @classmethod
    def get_active_agent_info(cls):
        key = cls.get_active_agent_key()
        return cls.agent_config.get("agents", {}).get(key, {})

    @classmethod
    def set_active_agent(cls, key):
        if key in cls.agent_config.get("agents", {}):
            cls.agent_config["active_agent"] = key
            cls.save_agent_config()
            cls.log_event("ui_action", f"Switched Active Agent to: {key}")
            if cls.tab_instance and hasattr(cls.tab_instance, "on_agent_changed"):
                cls.tab_instance.on_agent_changed(key)

    @classmethod
    def record_rpc_call(cls, method, params=None):
        cls.total_rpc_requests += 1
        cls.rpc_stats[method] = cls.rpc_stats.get(method, 0) + 1
        agent_name = cls.get_active_agent_info().get("name", "Agent")
        cls.log_event("agent_call", f"{agent_name} -> {method}()", params)
        if cls.tab_instance and hasattr(cls.tab_instance, "update_telemetry_ui"):
            cls.tab_instance.update_telemetry_ui()

    @classmethod
    def record_rpc_success(cls, method, res_preview=""):
        agent_name = cls.get_active_agent_info().get("name", "Agent")
        cls.log_event("agent_success", f"{agent_name} <- {method} OK {res_preview}")

    @classmethod
    def log_event(cls, event_type, message, extra=None):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        record = {
            "time": ts,
            "type": event_type,
            "message": message,
            "extra": str(extra) if extra else ""
        }
        cls.event_history.append(record)
        if len(cls.event_history) > 400:
            cls.event_history.pop(0)

        if cls.tab_instance and hasattr(cls.tab_instance, "append_log_safe"):
            cls.tab_instance.append_log_safe(record)

    @classmethod
    def test_mcp_connection(cls):
        lang = cls.get_language()
        try:
            t0 = time.time()
            payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "get_metadata", "params": []}).encode("utf-8")
            req = urllib.request.Request("http://127.0.0.1:13337/mcp", data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                dt = int((time.time() - t0) * 1000)
                if "result" in data:
                    mod = "IDA Binary"
                    if isinstance(data["result"], dict):
                        mod = data["result"].get("module", "IDA Binary")
                    msg = f"Online & Responsif ({dt} ms) - Modul: {mod}" if lang == "id" else f"Online & Responsive ({dt} ms) - Module: {mod}"
                    return True, msg
                return False, f"Server returned error: {data.get('error')}"
        except Exception as e:
            try:
                t0 = time.time()
                payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "check_connection", "params": []}).encode("utf-8")
                req = urllib.request.Request("http://127.0.0.1:13337/mcp", data=payload, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=2) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    dt = int((time.time() - t0) * 1000)
                    if "result" in data:
                        return True, f"Online & Responsif ({dt} ms)" if lang == "id" else f"Online & Responsive ({dt} ms)"
                    return False, f"Server returned error: {data.get('error')}"
            except Exception:
                pass
            return False, f"Connection failed: {e}"

    @classmethod
    def get_binary_info(cls):
        info = {
            "name": "No binary loaded",
            "path": "",
            "sha256": "N/A",
            "proc": "Unknown",
            "is_64": False,
            "imagebase": "0x0",
            "total_funcs": 0
        }
        try:
            import ida_nalt
            import ida_ida
            import ida_funcs

            root = ida_nalt.get_root_filename()
            if root:
                info["name"] = root

            path = ida_nalt.get_input_file_path()
            if path:
                info["path"] = path
                if os.path.isfile(path):
                    try:
                        with open(path, "rb") as f:
                            info["sha256"] = hashlib.sha256(f.read()).hexdigest()
                    except Exception:
                        pass

            if info["sha256"] == "N/A":
                try:
                    raw_hash = ida_nalt.retrieve_input_file_sha256()
                    if raw_hash:
                        info["sha256"] = raw_hash.hex()
                except Exception:
                    pass

            info["proc"] = ida_ida.inf_get_procname()
            info["is_64"] = ida_ida.inf_is_64bit()
            info["imagebase"] = hex(ida_nalt.get_imagebase())
            info["total_funcs"] = ida_funcs.get_func_qty()
        except Exception:
            pass
        return info

    @classmethod
    def get_current_function_details(cls, target_ea=None):
        try:
            import ida_kernwin
            import ida_funcs
            import ida_hexrays
            import ida_lines

            ea = target_ea if target_ea is not None else ida_kernwin.get_screen_ea()
            func = ida_funcs.get_func(ea)
            if not func:
                lang = cls.get_language()
                no_code_msg = "[Kursor saat ini berada di luar fungsi yang terdefinisi]" if lang == "id" else "[Cursor is currently outside a defined function]"
                return {
                    "name": "Outside Function",
                    "start": hex(ea),
                    "end": hex(ea),
                    "size": 0,
                    "pseudocode": no_code_msg,
                    "has_cff": False
                }

            fname = ida_funcs.get_func_name(func.start_ea)
            size = func.end_ea - func.start_ea
            pseudocode = ""
            has_cff = False

            try:
                error = ida_hexrays.hexrays_failure_t()
                cfunc = ida_hexrays.decompile_func(func, error, ida_hexrays.DECOMP_WARNINGS)
                if cfunc:
                    lines = []
                    for sl in cfunc.get_pseudocode():
                        raw_str = sl.line if hasattr(sl, "line") else str(sl)
                        lines.append(ida_lines.tag_remove(raw_str) if hasattr(ida_lines, "tag_remove") else raw_str)
                    pseudocode = "\n".join(lines)
                    label_count = len(re.findall(r"LABEL_\d+:", pseudocode))
                    goto_count = len(re.findall(r"goto\s+LABEL_\d+", pseudocode))
                    if label_count >= 6 and goto_count >= 6:
                        has_cff = True
            except Exception:
                pass

            if not pseudocode:
                try:
                    import idautils
                    lines = [f"[Disassembly for {fname} ({hex(func.start_ea)})]"]
                    for head in idautils.Heads(func.start_ea, min(func.start_ea + 128, func.end_ea)):
                        raw_d = ida_lines.generate_disasm_line(head, 0) if hasattr(ida_lines, "generate_disasm_line") else idaapi.generate_disasm_line(head, 0)
                        clean_d = ida_lines.tag_remove(raw_d) if (raw_d and hasattr(ida_lines, "tag_remove")) else str(raw_d)
                        lines.append(f"{hex(head)}: {clean_d}")
                    if func.end_ea - func.start_ea > 128:
                        lines.append("  ... [truncated for preview]")
                    pseudocode = "\n".join(lines)
                except Exception as e:
                    pseudocode = f"[Error generating code: {e}]"

            return {
                "name": fname,
                "start": hex(func.start_ea),
                "end": hex(func.end_ea),
                "size": size,
                "pseudocode": pseudocode,
                "has_cff": has_cff
            }
        except Exception:
            return None

    @classmethod
    def export_current_function_for_agent(cls):
        try:
            func_info = cls.get_current_function_details()
            if not func_info:
                return False, "No active function"

            agent_info = cls.get_active_agent_info()
            agent_name = agent_info.get("name", "User Agent")
            lang = cls.get_language()

            content = f"/* Function: {func_info['name']} ({func_info['start']} - {func_info['end']}, size: {func_info['size']} bytes) */\n"
            content += f"/* Prepared for: {agent_name} via IDA Pro MCP Server (http://127.0.0.1:13337/mcp) */\n\n"
            content += func_info.get("pseudocode", "[No code available]")

            os.makedirs(os.path.dirname(cls.func_export_file), exist_ok=True)
            with open(cls.func_export_file, "w", encoding="utf-8") as f:
                f.write(content)

            if QtWidgets:
                QtWidgets.QApplication.clipboard().setText(content)

            cls.export_full_context_for_agent()
            notice = f"Fungsi '{func_info['name']}' diekspor untuk {agent_name}" if lang == "id" else f"Function '{func_info['name']}' exported for {agent_name}"
            cls.log_event("ui_action", notice)
            return True, func_info["name"]
        except Exception as e:
            return False, str(e)

    @classmethod
    def export_full_context_for_agent(cls):
        try:
            import idautils
            import ida_funcs

            bin_info = cls.get_binary_info()
            func_info = cls.get_current_function_details()
            agent_info = cls.get_active_agent_info()

            func_list = []
            for i, ea in enumerate(idautils.Functions()):
                if i >= 150:
                    break
                name = ida_funcs.get_func_name(ea)
                func_list.append({"name": name, "ea": hex(ea)})

            context_data = {
                "generated": datetime.datetime.now().isoformat(),
                "agent": agent_info.get("name", "User Agent"),
                "language": cls.get_language(),
                "binary": bin_info,
                "current_focus": func_info,
                "function_sample": func_list
            }

            os.makedirs(os.path.dirname(cls.context_export_file), exist_ok=True)
            with open(cls.context_export_file, "w", encoding="utf-8") as f:
                json.dump(context_data, f, indent=2)

            cls.log_event("ui_action", "Exported full IDA analysis context to ida_agent_context.json")
            return True, "Export successful"
        except Exception as e:
            return False, str(e)

    @classmethod
    def inspect_function_semantics(cls, func_info):
        code = func_info.get("pseudocode", "")
        ea = int(func_info["start"], 16)
        fname = func_info["name"]
        size = func_info.get("size", 0)
        lang = cls.get_language()

        keywords = {
            "if", "while", "for", "switch", "return", "sizeof", "__asm", "case", "else", "goto", "do",
            "_BYTE", "_WORD", "_DWORD", "_QWORD", "_OWORD", "unsigned", "int", "char", "void", "bool",
            "__int64", "__int32", "__int16", "__int8", "__fastcall", "__cdecl", "__stdcall", "LL", "u"
        }
        raw_calls = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", code)
        calls = []
        for c in raw_calls:
            if c not in keywords and not c.startswith("__") and c != fname and c not in calls:
                calls.append(c)

        globals_accessed = []
        for g in re.findall(r"\b((?:qword|dword|byte|off|unk|loc|word)_[0-9a-fA-F]+)\b", code):
            if g not in globals_accessed:
                globals_accessed.append(g)

        labels = re.findall(r"LABEL_\d+:", code)
        gotos = re.findall(r"goto\s+LABEL_\d+", code)
        state_candidates = re.findall(r"(v\d+)\s*<\s*-?\d+", code)
        is_cff = (len(labels) >= 5 and len(gotos) >= 5) or (len(state_candidates) >= 2 and len(gotos) >= 3)
        has_jumpout = "JUMPOUT(" in code

        has_once = "pthread_once" in code or "once_control" in code
        has_alloc = any(k in code for k in ["malloc", "calloc", "operator new", "sub_519AE0", "alloc"])
        has_vtable = bool(re.search(r"\*\(_QWORD\s*\*\)\s*[a-zA-Z0-9_]+\s*=\s*&off_", code))
        checks_global_ptr = any(g.startswith("qword_") for g in globals_accessed) and ("!" in code or "== 0" in code)
        is_singleton = (has_once or (checks_global_ptr and has_alloc)) and not is_cff

        is_constructor = (has_vtable or has_alloc or bool(re.search(r"\*\([a-zA-Z0-9_]+\s*\+\s*\d+\)\s*=", code))) and not is_cff and not is_singleton

        xor_count = code.count("^")
        shift_count = code.count("<<") + code.count(">>")
        is_crypto = (xor_count >= 5 or shift_count >= 8) and not is_cff

        is_ioctl = any(k in code.lower() for k in ["ioctl", "sock", "/dev/", "ipc"]) or ("anosdk" in fname.lower() and "ioctl" in fname.lower())

        if is_cff:
            category = "OLLVM Control Flow Flattening (CFF)" if lang != "id" else "OLLVM Control Flow Flattening (CFF Terobfuskasi)"
            badge_color = "#e5c07b"
            badge_bg = "#3b2d1d"
        elif is_singleton:
            category = "Thread-Safe Singleton / Lazy Instance Factory" if lang != "id" else "Pabrik Instansi Tunggal Aman-Thread (Thread-Safe Singleton)"
            badge_color = "#56b6c2"
            badge_bg = "#1b3338"
        elif is_constructor:
            category = "Object Initializer / C++ Struct Setup" if lang != "id" else "Inisialisasi Objek / Setup Struktur C++"
            badge_color = "#c678dd"
            badge_bg = "#341f38"
        elif is_crypto:
            category = "Cryptographic / Hash / Arithmetic Transform" if lang != "id" else "Transformasi Kriptografi / Hash / Aritmatika"
            badge_color = "#d19a66"
            badge_bg = "#38291a"
        elif is_ioctl:
            category = "Kernel Driver / IOCTL Security Handler" if lang != "id" else "Driver Kernel / Pengendali Keamanan IOCTL"
            badge_color = "#e06c75"
            badge_bg = "#381c20"
        else:
            category = "Standard Linear Subroutine" if lang != "id" else "Subrutin Linear Standar"
            badge_color = "#98c379"
            badge_bg = "#1f3325"

        local_vars = []
        for match in re.finditer(r"\b([_a-zA-Z0-9]+)\s+(v\d+)(?:\[(\d+)\])?\s*;", code):
            vtype, vname, varr = match.group(1), match.group(2), match.group(3)
            local_vars.append({
                "type": f"{vtype}[{varr}]" if varr else vtype,
                "name": vname,
                "is_array": bool(varr)
            })

        caller_count = 0
        try:
            import ida_xref, idaapi
            xref = ida_xref.get_first_cref_to(ea)
            while xref != idaapi.BADADDR:
                caller_count += 1
                xref = ida_xref.get_next_cref_to(ea, xref)
        except Exception:
            pass

        return {
            "calls": calls,
            "globals": globals_accessed,
            "is_cff": is_cff,
            "is_singleton": is_singleton,
            "is_constructor": is_constructor,
            "is_crypto": is_crypto,
            "is_ioctl": is_ioctl,
            "has_jumpout": has_jumpout,
            "has_once": has_once,
            "has_alloc": has_alloc,
            "has_vtable": has_vtable,
            "category": category,
            "badge_color": badge_color,
            "badge_bg": badge_bg,
            "local_vars": local_vars,
            "labels": labels,
            "gotos": gotos,
            "state_candidates": state_candidates,
            "caller_count": caller_count
        }

    @classmethod
    def analyze_function_deobfuscation(cls):
        func_info = cls.get_current_function_details()
        lang = cls.get_language()
        if not func_info or not func_info.get("pseudocode"):
            return "No function or pseudocode available for analysis." if lang != "id" else "Tidak ada fungsi atau kode semu yang tersedia untuk dianalisis."

        fname = func_info["name"]
        code = func_info["pseudocode"]
        size = func_info["size"]
        ea_str = func_info["start"]

        sem = cls.inspect_function_semantics(func_info)

        rename_suggs = []
        if sem["is_singleton"]:
            alloc_var_match = re.search(r"\b(v\d+)\s*=\s*(?:sub_519AE0|malloc|calloc|operator new)", code)
            alloc_var = alloc_var_match.group(1) if alloc_var_match else "v0"
            for lv in sem["local_vars"]:
                vn = lv["name"]
                if vn in [r[0] for r in rename_suggs]:
                    continue
                if lv["is_array"]:
                    desc_s = "16-byte stack parameter buffer for object initialization" if lang != "id" else "Buffer parameter stack 16-byte untuk inisialisasi objek"
                    rename_suggs.append((vn, "init_param_buf", desc_s))
                elif vn == alloc_var:
                    desc_s = "Heap allocated instance object pointer (264 bytes)" if lang != "id" else "Pointer objek instansi hasil alokasi heap (264 byte)"
                    rename_suggs.append((vn, "p_singleton_inst", desc_s))
                elif "qword_" in code:
                    desc_s = "Existing global instance object cache pointer" if lang != "id" else "Pointer penampung instansi objek global yang sudah ada"
                    rename_suggs.append((vn, "cached_global_inst", desc_s))
                else:
                    desc_s = "Temporary variable during singleton initialization" if lang != "id" else "Variabel sementara saat inisialisasi singleton"
                    rename_suggs.append((vn, f"inst_var_{vn}", desc_s))
        elif sem["is_cff"]:
            if sem["state_candidates"]:
                s_var = sem["state_candidates"][0]
                desc_s = "Control flow flattening main state dispatcher variable" if lang != "id" else "Variabel pengatur status utama perataan alur kontrol"
                rename_suggs.append((s_var, "cff_state_dispatcher", desc_s))
                if len(sem["state_candidates"]) > 1:
                    sec_var = [v for v in sem["state_candidates"] if v != s_var][0]
                    desc_s = "Secondary state branch selector variable" if lang != "id" else "Variabel pemilih cabang status sekunder"
                    rename_suggs.append((sec_var, f"cff_substate_{sec_var}", desc_s))
            for lv in sem["local_vars"]:
                if lv["is_array"] and lv["name"] not in [r[0] for r in rename_suggs]:
                    desc_s = "Local stack memory buffer for payload/IPC" if lang != "id" else "Buffer memori stack lokal untuk muatan/IPC"
                    rename_suggs.append((lv["name"], "payload_data_buf", desc_s))
            for lv in sem["local_vars"]:
                if lv["name"] not in [r[0] for r in rename_suggs] and len(rename_suggs) < 6:
                    desc_s = "Intermediate calculation variable in flattened block" if lang != "id" else "Variabel komputasi perantara dalam blok terobfuskasi"
                    rename_suggs.append((lv["name"], f"cff_intermediate_{lv['name']}", desc_s))
        elif sem["is_constructor"]:
            for lv in sem["local_vars"]:
                if lv["is_array"]:
                    desc_s = "Initial parameter seed buffer for constructor" if lang != "id" else "Buffer bibit parameter awal untuk konstruktor"
                    rename_suggs.append((lv["name"], "struct_seed_buf", desc_s))
                elif lv["name"] not in [r[0] for r in rename_suggs]:
                    desc_s = "Pointer / member temporary value in struct initializer" if lang != "id" else "Nilai sementara pointer atau anggota pada inisialisasi struktur"
                    rename_suggs.append((lv["name"], f"obj_member_{lv['name']}", desc_s))
        elif sem["is_crypto"]:
            for lv in sem["local_vars"]:
                if lv["is_array"]:
                    desc_s = "Input/output buffer block for cryptographic calculation" if lang != "id" else "Buffer blok masukan/keluaran untuk kalkulasi kriptografi"
                    rename_suggs.append((lv["name"], "cipher_block_buf", desc_s))
                else:
                    desc_s = "Hash state accumulator / bitwise mask" if lang != "id" else "Akumulator status hash atau mask bitwise"
                    rename_suggs.append((lv["name"], f"hash_state_{lv['name']}", desc_s))
        else:
            for lv in sem["local_vars"]:
                if lv["is_array"]:
                    desc_s = "Stack memory working buffer" if lang != "id" else "Buffer kerja memori stack"
                    rename_suggs.append((lv["name"], f"stack_buf_{lv['name']}", desc_s))
                elif "return " + lv["name"] in code:
                    desc_s = "Return value status code" if lang != "id" else "Kode status nilai kembalian"
                    rename_suggs.append((lv["name"], f"ret_status_{lv['name']}", desc_s))
                else:
                    desc_s = "Temporary intermediate computation variable" if lang != "id" else "Variabel komputasi perantara sementara"
                    rename_suggs.append((lv["name"], f"arg_or_tmp_{lv['name']}", desc_s))

        cls.last_rename_suggestions = rename_suggs

        if lang == "id":
            if sem["is_singleton"]:
                vtable_str = next((g for g in sem['globals'] if 'off_' in g), "vtable")
                g_ptr = next((g for g in sem['globals'] if 'qword_' in g), "qword_global")
                desc = f"Fungsi ini merupakan <b>Penyedia Objek / Singleton Aman-Thread</b>. Fungsinya menjamin bahwa struktur objek instance utama hanya dialokasikan tepat satu kali di memori heap (menggunakan <code>pthread_once</code>) dan kemudian dicache pada pointer global <code>{g_ptr}</code> agar dapat diakses oleh seluruh thread game/driver."
                wf_title = "Alur Kerja Inisialisasi Singleton (Thread-Safe Lazy Initialization):"
                wf_steps = [
                    f"<b>Pemeriksaan Cepat Cache:</b> Memeriksa apakah pointer global <code>{g_ptr}</code> telah dialokasikan sebelumnya. Jika sudah ada, langsung return instance tanpa locking overhead.",
                    f"<b>Rutinitas Aman-Thread:</b> Jika masih NULL, memanggil <code>pthread_once</code> bersama fungsi rutin <code>{sem['calls'][1] if len(sem['calls']) > 1 else 'init_func'}</code> agar alokasi aman terhadap race condition.",
                    "<b>Alokasi Heap:</b> Mengalokasikan memori heap berukuran 264 byte melalui sub-alokator <code>sub_519AE0(264LL)</code>.",
                    f"<b>Konstruksi Objek & VTable:</b> Memanggil sub-konstruktor, memasang virtual method table <code>{vtable_str}</code>, dan mengonfigurasi offset anggota struktur.",
                    f"<b>Penyimpanan Cache & Pengembalian:</b> Menyimpan pointer objek yang telah siap ke variabel global <code>{g_ptr}</code> dan mengembalikannya ke caller."
                ]
                strat_hook = f"""
                <li><b>Hook Nilai Kembalian:</b> Pasang Frida hook pada return fungsi <code>{fname}</code> (<code>{ea_str}</code>) untuk memperoleh pointer memori instansi objek yang dialokasikan.</li>
                <li><b>Inspeksi VTable Objek:</b> Pointer vtable <code>{vtable_str}</code> dapat dieksplorasi di memori untuk memetakan fungsi-fungsi virtual objek ini.</li>
                """
            elif sem["is_cff"]:
                desc = "Fungsi ini terobfuskasi menggunakan <b>OLLVM Control Flow Flattening (CFF)</b>. Alur percabangan dan perulangan telah dipecah menjadi basic blocks independen yang dikendalikan oleh variabel dispatcher state machine."
                wf_title = "Struktur Eksekusi Mesin Status (De-flattened):"
                s_var = sem["state_candidates"][0] if sem["state_candidates"] else "dispatcher_var"
                wf_steps = [
                    f"<b>Status Awal (Inisialisasi Dispatcher):</b> Menginisialisasi variabel state <code>{s_var}</code> dengan nilai awal.",
                    "<b>Perulangan Dispatch Status:</b> Evaluasi kondisi state di dalam switch/if-else tree untuk mengarahkan eksekusi ke basic block berikutnya.",
                    "<b>Eksekusi Blok:</b> Menjalankan komputasi logika, verifikasi memory, atau transmisi payload di dalam basic block.",
                    "<b>Transisi & Terminasi:</b> Memperbarui nilai state dispatcher hingga mencapai terminal return state."
                ]
                strat_hook = f"""
                <li><b>Frida Interceptor:</b> Pasang hook pada <code>{ea_str}</code> untuk memonitor parameter input dan return value.</li>
                <li><b>Bypass Status:</b> Jika fungsi memverifikasi integritas, ubah return value pada hook <code>onLeave</code> menjadi nilai sukses (misal: 0).</li>
                """
            elif sem["is_constructor"]:
                desc = "Fungsi ini merupakan <b>Inisialisasi Objek / Konstruktor Struktur C++</b>. Berfungsi memasang tabel vtable method dan menginisialisasi properti objek."
                wf_title = "Alur Konstruksi Struktur Objek (Object Initialization):"
                vtable_str = next((g for g in sem['globals'] if 'off_' in g), "vtable")
                wf_steps = [
                    "<b>Persiapan Buffer:</b> Menyiapkan pointer objek target dari parameter input atau heap.",
                    f"<b>Pengaitan VTable:</b> Mengaitkan virtual method table <code>{vtable_str}</code> ke offset 0x0 objek.",
                    "<b>Inisialisasi Field:</b> Menulis nilai atribut properti default ke offset internal objek dan melakukan zeroing memori."
                ]
                strat_hook = f"""
                <li><b>Hook Pointer Instansi:</b> Monitor argumen pertama (X0 / R0) yang merupakan <code>this</code> pointer dari objek yang sedang dibuat.</li>
                """
            elif sem["is_crypto"]:
                desc = "Fungsi ini menjalankan <b>Transformasi Kriptografi / Hash / Operasi Bitwise</b>. Menggunakan permutasi bitwise intensif (XOR, shift) untuk enkripsi data atau kalkulasi checksum integritas."
                wf_title = "Alur Transformasi Kriptografi:"
                wf_steps = [
                    "<b>Setup Masukan & Status:</b> Memuat konstanta hash atau seed awal ke register working state.",
                    "<b>Permutasi Bitwise:</b> Rangkaian perulangan kalkulasi bitwise XOR, shift kiri/kanan, dan masking aritmatika.",
                    "<b>Digest Akhir:</b> Menggabungkan hasil transformasi ke buffer output atau return value."
                ]
                strat_hook = f"""
                <li><b>Dump Buffer Input/Output:</b> Pasang hook pada <code>onEnter</code> dan <code>onLeave</code> untuk mencatat buffer sebelum dan sesudah di-transformasi.</li>
                """
            else:
                desc = f"Fungsi ini memiliki alur eksekusi <b>Subrutin Linear / Logika Standar</b> (Ukuran: {size} byte). Kode berjalan secara sekuensial tanpa pengaburan alur kontrol buatan."
                wf_title = "Alur Eksekusi Fungsi Sekuensial (Linear Flow):"
                calls_label = ", ".join(sem["calls"][:3]) if sem["calls"] else "operasi aritmatika internal"
                wf_steps = [
                    "<b>Prolog:</b> Menyiapkan stack frame dan mengalokasikan ruang untuk variabel lokal.",
                    f"<b>Eksekusi Sub-rutin:</b> Memanggil sub-fungsi ({calls_label}) dan mengevaluasi percabangan logika.",
                    "<b>Epilog & Kembalian:</b> Menyelesaikan komputasi dan mengembalikan hasil ke pemanggil."
                ]
                strat_hook = f"""
                <li><b>Pelacakan Parameter:</b> Pasang Frida hook pada <code>{ea_str}</code> untuk membaca argumen register X0-X3 saat fungsi dipanggil.</li>
                """

            html = f"""
            <div style="font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.6; color: #dcdde4;">
                <div style="border-bottom: 1px solid #333342; padding-bottom: 8px; margin-bottom: 12px;">
                    <span style="font-size: 15px; font-weight: bold; color: #61afef;">⚡ Analisis Semantik: {fname}</span>
                    <span style="font-size: 11px; color: #8c8f9e; margin-left: 10px;">Rentang: {func_info['start']} - {func_info['end']} ({size} byte)</span>
                </div>
                
                <div style="margin-bottom: 10px;">
                    <b>Klasifikasi:</b> 
                    <span style="background-color: {sem['badge_bg']}; color: {sem['badge_color']}; padding: 3px 10px; border-radius: 4px; font-weight: bold;">{sem['category']}</span>
                    {"<span style='background-color: #331f24; color: #e06c75; padding: 2px 8px; border-radius: 4px; margin-left: 6px;'>Terdeteksi JUMPOUT Tak Terselesaikan</span>" if sem['has_jumpout'] else ""}
                </div>

                <p style="color: #abb2bf;">
                    <b>Fungsi Utama:</b> {desc}
                </p>

                <div style="background-color: #1f1f28; border-left: 3px solid #61afef; padding: 8px 12px; margin: 10px 0; border-radius: 0 4px 4px 0;">
                    <b style="color: #61afef;">{wf_title}</b>
                    <ol style="margin: 6px 0 6px 18px; padding: 0; color: #bcc0cc;">
            """
            for step in wf_steps:
                html += f"<li>{step}</li>"

            html += f"""
                    </ol>
                </div>

                <div style="margin-top: 12px;">
                    <b style="color: #98c379;">💡 Saran Ubah Nama Variabel untuk IDA Pro:</b>
                    <table style="width: 100%; margin-top: 6px; border-collapse: collapse; font-size: 11px;">
                        <tr style="color: #7f848e; text-align: left; border-bottom: 1px solid #2d2d3a;">
                            <th style="padding: 4px;">Var Asli</th>
                            <th style="padding: 4px;">Saran Nama Baru</th>
                            <th style="padding: 4px;">Tujuan & Keterangan</th>
                        </tr>
            """
            if rename_suggs:
                for orig, sugg, desc_s in rename_suggs:
                    html += f"""
                        <tr style="border-bottom: 1px solid #252532;">
                            <td style="padding: 5px; color: #e06c75; font-family: monospace;">{orig}</td>
                            <td style="padding: 5px; color: #98c379; font-weight: bold; font-family: monospace;">{sugg}</td>
                            <td style="padding: 5px; color: #abb2bf;">{desc_s}</td>
                        </tr>
                    """
            else:
                html += """
                        <tr><td colspan="3" style="padding: 6px; color: #7f848e;">Tidak ada variabel obfuskasi agresif yang perlu diubah namanya secara otomatis.</td></tr>
                """
            html += f"""
                    </table>
                </div>

                <div style="margin-top: 14px; padding: 8px 12px; background-color: #1a221f; border-left: 3px solid #10b981; border-radius: 0 4px 4px 0;">
                    <b style="color: #10b981;">🛡️ Strategi Reverse Engineering / Hooking:</b>
                    <ul style="margin: 4px 0 4px 18px; padding: 0; color: #abb2bf;">
                        {strat_hook}
                    </ul>
                </div>
            </div>
            """
            return html
        else:
            if sem["is_singleton"]:
                vtable_str = next((g for g in sem['globals'] if 'off_' in g), "vtable")
                g_ptr = next((g for g in sem['globals'] if 'qword_' in g), "qword_global")
                desc = f"This function is a <b>Thread-Safe Singleton Factory / Object Provider</b>. It ensures that the primary instance object is allocated exactly once in heap memory (using <code>pthread_once</code>) and cached in global pointer <code>{g_ptr}</code> so that it can be accessed across all game/driver threads."
                wf_title = "Singleton Initialization Flow (Thread-Safe Lazy Initialization):"
                wf_steps = [
                    f"<b>Fast-Path Cache Check:</b> Checks whether global pointer <code>{g_ptr}</code> was previously allocated. If non-null, returns the instance immediately without locking overhead.",
                    f"<b>Thread-Safe Routine:</b> If NULL, calls <code>pthread_once</code> with routine <code>{sem['calls'][1] if len(sem['calls']) > 1 else 'init_func'}</code> to prevent race conditions during allocation.",
                    "<b>Heap Allocation:</b> Allocates 264 bytes of heap memory via sub-allocator <code>sub_519AE0(264LL)</code>.",
                    f"<b>Object Construction & VTable:</b> Invokes sub-constructor, binds virtual method table <code>{vtable_str}</code>, and initializes struct member offsets.",
                    f"<b>Cache Storage & Return:</b> Stores ready object pointer into global variable <code>{g_ptr}</code> and returns it to caller."
                ]
                strat_hook = f"""
                <li><b>Hook Return Value:</b> Attach Frida hook to function return of <code>{fname}</code> (<code>{ea_str}</code>) to obtain allocated object memory pointer.</li>
                <li><b>Inspect Object VTable:</b> VTable pointer <code>{vtable_str}</code> can be inspected in memory to map virtual methods of this object.</li>
                """
            elif sem["is_cff"]:
                desc = "This function is obfuscated using <b>OLLVM Control Flow Flattening (CFF)</b>. Branches and loops are broken into independent basic blocks controlled by a dispatcher state variable."
                wf_title = "State Machine Execution Flow (De-flattened):"
                s_var = sem["state_candidates"][0] if sem["state_candidates"] else "dispatcher_var"
                wf_steps = [
                    f"<b>State 0 (Dispatcher Initialization):</b> Initializes state variable <code>{s_var}</code> with entry value.",
                    "<b>State Dispatch Loop:</b> Evaluates state conditions inside switch/if-else tree to dispatch to the next basic block.",
                    "<b>Block Execution:</b> Executes core logic, memory verification, or payload transmission inside basic block.",
                    "<b>State Transition & Exit:</b> Updates state dispatcher value until terminal return state is reached."
                ]
                strat_hook = f"""
                <li><b>Frida Interceptor:</b> Attach hook at <code>{ea_str}</code> to monitor input arguments and return value.</li>
                <li><b>Bypass State:</b> If function performs security verification, override return value in <code>onLeave</code> to success status (e.g., 0).</li>
                """
            elif sem["is_constructor"]:
                desc = "This function is a <b>C++ Object Initializer / Struct Constructor</b>. It binds the virtual method table and initializes struct member properties."
                wf_title = "Object Struct Construction Flow (Object Initialization):"
                vtable_str = next((g for g in sem['globals'] if 'off_' in g), "vtable")
                wf_steps = [
                    "<b>Buffer Setup:</b> Sets up target object pointer from input parameters or heap.",
                    f"<b>VTable Binding:</b> Binds virtual method table <code>{vtable_str}</code> to object offset 0x0.",
                    "<b>Field Initialization:</b> Writes default member property values into internal struct offsets and clears padding memory."
                ]
                strat_hook = f"""
                <li><b>Hook Instance Pointer:</b> Monitor first argument (X0 / R0) representing the <code>this</code> pointer of the created object.</li>
                """
            elif sem["is_crypto"]:
                desc = "This function executes a <b>Cryptographic / Hash / Bitwise Transformation</b>. It uses intensive bitwise permutations (XOR, shift) for encryption or integrity checksum calculation."
                wf_title = "Cryptographic Transformation Flow:"
                wf_steps = [
                    "<b>Input & State Setup:</b> Loads hash constants or initial seed into working state registers.",
                    "<b>Bitwise Permutations:</b> Iterative loops of bitwise XOR, shifts, and arithmetic masking.",
                    "<b>Final Digest:</b> Combines transformed state into output buffer or return value."
                ]
                strat_hook = f"""
                <li><b>Dump Buffer Input/Output:</b> Attach hooks on <code>onEnter</code> and <code>onLeave</code> to capture buffers before and after transformation.</li>
                """
            else:
                desc = f"This function exhibits a <b>Linear Subroutine / Standard Logic</b> flow (Size: {size} bytes). Execution proceeds sequentially without artificial control flow obfuscation."
                wf_title = "Sequential Function Execution (Linear Flow):"
                calls_label = ", ".join(sem["calls"][:3]) if sem["calls"] else "internal arithmetic operations"
                wf_steps = [
                    "<b>Prologue:</b> Sets up stack frame and allocates space for local variables.",
                    f"<b>Subroutine Calls:</b> Calls helper routines ({calls_label}) and evaluates branching conditions.",
                    "<b>Epilogue & Return:</b> Completes computation and returns result to caller."
                ]
                strat_hook = f"""
                <li><b>Parameter Tracing:</b> Attach Frida hook at <code>{ea_str}</code> to read register arguments X0-X3 upon invocation.</li>
                """

            html = f"""
            <div style="font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.6; color: #dcdde4;">
                <div style="border-bottom: 1px solid #333342; padding-bottom: 8px; margin-bottom: 12px;">
                    <span style="font-size: 15px; font-weight: bold; color: #61afef;">⚡ Semantic Analysis: {fname}</span>
                    <span style="font-size: 11px; color: #8c8f9e; margin-left: 10px;">Range: {func_info['start']} - {func_info['end']} ({size} bytes)</span>
                </div>
                
                <div style="margin-bottom: 10px;">
                    <b>Classification:</b> 
                    <span style="background-color: {sem['badge_bg']}; color: {sem['badge_color']}; padding: 3px 10px; border-radius: 4px; font-weight: bold;">{sem['category']}</span>
                    {"<span style='background-color: #331f24; color: #e06c75; padding: 2px 8px; border-radius: 4px; margin-left: 6px;'>Unresolved JUMPOUT Detected</span>" if sem['has_jumpout'] else ""}
                </div>

                <p style="color: #abb2bf;">
                    <b>Primary Purpose:</b> {desc}
                </p>

                <div style="background-color: #1f1f28; border-left: 3px solid #61afef; padding: 8px 12px; margin: 10px 0; border-radius: 0 4px 4px 0;">
                    <b style="color: #61afef;">{wf_title}</b>
                    <ol style="margin: 6px 0 6px 18px; padding: 0; color: #bcc0cc;">
            """
            for step in wf_steps:
                html += f"<li>{step}</li>"

            html += f"""
                    </ol>
                </div>

                <div style="margin-top: 12px;">
                    <b style="color: #98c379;">💡 Variable Rename Suggestions for IDA Pro:</b>
                    <table style="width: 100%; margin-top: 6px; border-collapse: collapse; font-size: 11px;">
                        <tr style="color: #7f848e; text-align: left; border-bottom: 1px solid #2d2d3a;">
                            <th style="padding: 4px;">Original Var</th>
                            <th style="padding: 4px;">Suggested Name</th>
                            <th style="padding: 4px;">Purpose & Description</th>
                        </tr>
            """
            if rename_suggs:
                for orig, sugg, desc_s in rename_suggs:
                    html += f"""
                        <tr style="border-bottom: 1px solid #252532;">
                            <td style="padding: 5px; color: #e06c75; font-family: monospace;">{orig}</td>
                            <td style="padding: 5px; color: #98c379; font-weight: bold; font-family: monospace;">{sugg}</td>
                            <td style="padding: 5px; color: #abb2bf;">{desc_s}</td>
                        </tr>
                    """
            else:
                html += """
                        <tr><td colspan="3" style="padding: 6px; color: #7f848e;">No aggressive obfuscated variables requiring automatic renaming.</td></tr>
                """
            html += f"""
                    </table>
                </div>

                <div style="margin-top: 14px; padding: 8px 12px; background-color: #1a221f; border-left: 3px solid #10b981; border-radius: 0 4px 4px 0;">
                    <b style="color: #10b981;">🛡️ Reverse Engineering / Hooking Strategy:</b>
                    <ul style="margin: 4px 0 4px 18px; padding: 0; color: #abb2bf;">
                        {strat_hook}
                    </ul>
                </div>
            </div>
            """
            return html

    @classmethod
    def apply_renames_to_ida(cls):
        lang = cls.get_language()
        try:
            import idaapi
            import ida_hexrays

            func_info = cls.get_current_function_details()
            if not func_info:
                return False, "No active function selected." if lang != "id" else "Tidak ada fungsi aktif yang dipilih."

            ea = int(func_info["start"], 16)
            renamed_count = 0
            for orig, sugg, _ in cls.last_rename_suggestions:
                try:
                    if ida_hexrays.rename_lvar(ea, orig, sugg):
                        renamed_count += 1
                except Exception as ex:
                    print(f"[MCP Dashboard] Notice renaming {orig} -> {sugg}: {ex}")

            try:
                error = ida_hexrays.hexrays_failure_t()
                cfunc = ida_hexrays.decompile_func(ea, error, ida_hexrays.DECOMP_WARNINGS)
                if cfunc:
                    cfunc.refresh_func_ctext()
            except Exception:
                pass

            try:
                import ida_kernwin
                widget = ida_kernwin.get_current_widget()
                if widget:
                    vu = ida_hexrays.get_widget_vdui(widget)
                    if vu:
                        vu.refresh_view(True)
                ida_kernwin.request_refresh(ida_kernwin.IWID_ALL)
            except Exception:
                pass

            cmt = f"[MCP Dashboard] Analyzed: {func_info['name']} (Renamed {renamed_count} variables via MCP Assistant)"
            idaapi.set_func_cmt(idaapi.get_func(ea), cmt, False)

            if cls.tab_instance and hasattr(cls.tab_instance, "refresh_cursor_info"):
                cls.tab_instance.refresh_cursor_info(ea)

            cls.log_event("agent_success", f"Applied {renamed_count} variable renames & comment to {func_info['name']}")
            msg = f"Berhasil mengubah nama {renamed_count} variabel di tampilan Hex-Rays IDA!" if lang == "id" else f"Successfully renamed {renamed_count} variables in Hex-Rays IDA view!"
            return True, msg
        except Exception as e:
            return False, str(e)

    @classmethod
    def query_direct_llm(cls, query, func_info):
        direct_cfg = cls.agent_config.get("agents", {}).get("Direct_API", {})
        provider = direct_cfg.get("provider", "Gemini")
        api_key = direct_cfg.get("api_key") or cls.decode_secret(direct_cfg.get("api_key_enc", ""))
        api_key = api_key.strip() if api_key else ""
        model = direct_cfg.get("model", "").strip()
        base_url = direct_cfg.get("base_url", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        ollama_url = direct_cfg.get("ollama_url", "http://localhost:11434/v1").rstrip("/")
        lang = cls.get_language()

        if not model:
            if provider.lower() == "gemini":
                model = "gemini-2.0-flash"
            else:
                model = "gpt-4o"

        if lang == "id":
            system_instruction = (
                "Anda adalah AI Asisten Reverse Engineering tingkat lanjut di IDA Pro. "
                f"Fungsi Aktif: {func_info.get('name')} (Alamat: {func_info.get('start')}). "
                "Analisis assembly/pseudocode, jelaskan deobfuskasi control flow flattening (CFF), identifikasi fungsi kriptografi, "
                "rekomendasikan script hook Frida atau patch biner, dan berikan saran nama variabel dalam Bahasa Indonesia teknis yang akurat dan padat."
            )
        else:
            system_instruction = (
                "You are an elite Reverse Engineering AI Copilot assisting in IDA Pro. "
                f"Active Function: {func_info.get('name')} (Address: {func_info.get('start')}). "
                "Analyze the assembly/pseudocode, explain control flow flattening (CFF), identify cryptographic routines, "
                "recommend Frida hooks or binary patches, and suggest semantic variable renames in concise, technical English."
            )

        user_content = f"{system_instruction}\\n\\nPseudocode:\\n{func_info.get('pseudocode', '')[:8000]}\\n\\nUser Question: {query}"

        try:
            if provider.lower() == "gemini":
                if not api_key:
                    err_msg = "Kunci API Gemini kosong. Konfigurasikan pada tab 'Server MCP & Agen'." if lang == "id" else "Gemini API key is empty. Configure it in the 'MCP Server & Agents' tab."
                    return None, err_msg
                url = f"{base_url}/models/{model}:generateContent?key={api_key}"
                payload = {
                    "contents": [{"parts": [{"text": user_content}]}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2048}
                }
                req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=18) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    text = res["candidates"][0]["content"]["parts"][0]["text"]
                    return True, text
            else:
                target_url = f"{ollama_url}/chat/completions" if provider == "Ollama" else (f"{base_url}/chat/completions" if not base_url.endswith("/chat/completions") else base_url)
                headers = {"Content-Type": "application/json", "User-Agent": "IDAPRO-MCP-Client/2.0"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": f"Pseudocode:\\n{func_info.get('pseudocode','')[:8000]}\\n\\n{query}"}
                    ],
                    "max_tokens": 2048,
                    "temperature": 0.2
                }
                req = urllib.request.Request(target_url, data=json.dumps(payload).encode("utf-8"), headers=headers)
                with urllib.request.urlopen(req, timeout=25) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    text = res["choices"][0]["message"]["content"]
                    return True, text
        except urllib.error.HTTPError as ex:
            clean_msg = cls._format_http_error(ex, api_key)
            return False, f"Direct API Error ({provider}): {clean_msg}"
        except Exception as ex:
            clean_err = str(ex).replace(api_key, "sk-***") if api_key else str(ex)
            return False, f"Direct API Error ({provider}): {clean_err}"

    @classmethod
    def answer_user_query(cls, query):
        lang = cls.get_language()
        func_info = cls.get_current_function_details()
        if not func_info:
            return "Tidak ada fungsi yang sedang aktif di IDA View." if lang == "id" else "No active function in IDA View."

        fname = func_info.get("name", "Unknown")
        code = func_info.get("pseudocode", "")
        size = func_info.get("size", 0)
        ea_str = func_info.get("start", "0x0")
        ea = int(ea_str, 16)
        q_lower = query.lower().strip()

        active_key = cls.get_active_agent_key()
        agent_info = cls.get_active_agent_info()
        agent_name = agent_info.get("name", active_key)

        if active_key == "Direct_API":
            ok, direct_res = cls.query_direct_llm(query, func_info)
            if ok:
                cls.log_event("agent_success", f"Direct AI ({agent_info.get('provider')}) answered query")
                return f"""
                <div style="font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.5; color: #dcdde4;">
                    <div style="border-bottom: 1px solid #333342; padding-bottom: 4px; margin-bottom: 8px;">
                        <b style="color: #61afef;">Response from {agent_info.get('provider')} ({agent_info.get('model')}):</b>
                    </div>
                    <div style="white-space: pre-wrap; font-size: 12px;">{direct_res.replace('<', '&lt;').replace('>', '&gt;')}</div>
                </div>
                """
            cls.log_event("agent_error", direct_res or "Direct AI request failed")
            err_head = f"Gagal Menghubungi Custom AI Agent ({agent_info.get('provider', 'Direct API')} - {agent_info.get('model', 'Model')}):" if lang == "id" else f"Failed to Contact Custom AI Agent ({agent_info.get('provider', 'Direct API')} - {agent_info.get('model', 'Model')}):"
            err_sub = "Silakan periksa URL Basis, Kunci API, atau pilih Model ID lain pada tab Server MCP & Agen." if lang == "id" else "Please check Base URL, API Key, or select another Model ID in the MCP Server & Agents tab."
            return f"""
            <div style="font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.5; color: #EF4444; background-color: #2D1515; border: 1px solid #DC2626; border-radius: 6px; padding: 10px;">
                <b>❌ {err_head}</b><br/>
                <div style="margin-top: 6px; color: #FCA5A5; font-size: 12px;">{direct_res}</div>
                <div style="margin-top: 8px; font-size: 11px; color: #94A3B8;">{err_sub}</div>
            </div>
            """

        sem = cls.inspect_function_semantics(func_info)

        callers = []
        try:
            import ida_xref, ida_funcs, idaapi
            xref = ida_xref.get_first_cref_to(ea)
            while xref != idaapi.BADADDR:
                cfn = ida_funcs.get_func_name(xref)
                if cfn and cfn not in [c['name'] for c in callers]:
                    callers.append({"name": cfn, "address": hex(xref)})
                xref = ida_xref.get_next_cref_to(ea, xref)
        except Exception:
            pass

        if any(w in q_lower for w in ["bypass", "hook", "frida", "patch", "interceptor"]):
            if lang == "id":
                if sem["is_singleton"]:
                    return f"""
                    <div style="font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.5;">
                        <b style="color: #61afef;">🎯 Strategi Hooking Frida untuk Singleton <code>{fname}</code> (Disiapkan untuk {agent_name}):</b><br/>
                        Karena fungsi ini mengembalikan pointer instance objek tunggal, kita dapat meng-intercept return value untuk mengakses instance memori:<br/>
                        <pre style="background-color: #14141c; padding: 10px; border-radius: 6px; color: #98c379; font-family: monospace; font-size: 11px; margin: 6px 0;">
var targetAddr = Module.findExportByName("libanogs.so", "{fname}") || ptr("{ea_str}");
Interceptor.attach(targetAddr, {{
    onEnter: function(args) {{
        console.log("[+] {fname} dipanggil");
    }},
    onLeave: function(retval) {{
        console.log("[+] {fname} Return Instance: " + retval);
        if (!retval.isNull()) {{
            console.log(hexdump(retval, {{ length: 64, header: true }}));
        }}
    }}
}});
                        </pre>
                        <b style="color: #e5c07b;">📌 Catatan Reversing:</b> Objek ini memiliki vtable di <code>{next((g for g in sem['globals'] if 'off_' in g), 'vtable')}</code>. Anda dapat meng-hook fungsi-fungsi virtual di dalam vtable tersebut.
                    </div>
                    """
                elif sem["is_cff"] or sem["is_ioctl"]:
                    return f"""
                    <div style="font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.5;">
                        <b style="color: #61afef;">🛡️ Strategi Bypass & Hooking untuk <code>{fname}</code>:</b><br/>
                        <ol style="margin: 4px 0 6px 18px; padding: 0;">
                            <li><b>Frida Interceptor Hook:</b>
                                <pre style="background-color: #14141c; padding: 8px; border-radius: 4px; color: #98c379; font-family: monospace; font-size: 11px; margin: 4px 0;">
Interceptor.attach(Module.findExportByName("libanogs.so", "{fname}") || ptr("{ea_str}"), {{
    onEnter: function(args) {{
        console.log("[*] {fname} security check entered");
    }},
    onLeave: function(retval) {{
        console.log("[*] {fname} Original return: " + retval);
        retval.replace(ptr(0));
    }}
}});
                                </pre>
                            </li>
                            <li><b>Patch Biner (ARM64):</b> Ganti instruksi pertama di <code>{ea_str}</code> menjadi <code>MOV W0, #0; RET</code> (Hex bytes: <code>00 00 80 52 C0 03 5F D6</code>).</li>
                        </ol>
                    </div>
                    """
                else:
                    return f"""
                    <div style="font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.5;">
                        <b style="color: #61afef;">🔧 Skrip Frida untuk <code>{fname}</code>:</b><br/>
                        <pre style="background-color: #14141c; padding: 8px; border-radius: 4px; color: #98c379; font-family: monospace; font-size: 11px; margin: 4px 0;">
Interceptor.attach(ptr("{ea_str}"), {{
    onEnter: function(args) {{
        console.log("[*] {fname} dipanggil arg0=" + args[0] + ", arg1=" + args[1]);
    }},
    onLeave: function(retval) {{
        console.log("[*] {fname} return: " + retval);
    }}
}});
                        </pre>
                    </div>
                    """
            else:
                if sem["is_singleton"]:
                    return f"""
                    <div style="font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.5;">
                        <b style="color: #61afef;">🎯 Frida Hooking Strategy for Singleton <code>{fname}</code> (Prepared for {agent_name}):</b><br/>
                        Since this routine returns a singleton instance object pointer, intercept the return value to explore instance memory:<br/>
                        <pre style="background-color: #14141c; padding: 10px; border-radius: 6px; color: #98c379; font-family: monospace; font-size: 11px; margin: 6px 0;">
var targetAddr = Module.findExportByName("libanogs.so", "{fname}") || ptr("{ea_str}");
Interceptor.attach(targetAddr, {{
    onEnter: function(args) {{
        console.log("[+] {fname} called");
    }},
    onLeave: function(retval) {{
        console.log("[+] {fname} Return Instance: " + retval);
        if (!retval.isNull()) {{
            console.log(hexdump(retval, {{ length: 64, header: true }}));
        }}
    }}
}});
                        </pre>
                        <b style="color: #e5c07b;">📌 Reversing Note:</b> Object binds vtable at <code>{next((g for g in sem['globals'] if 'off_' in g), 'vtable')}</code>. You can hook virtual methods within that table.
                    </div>
                    """
                elif sem["is_cff"] or sem["is_ioctl"]:
                    return f"""
                    <div style="font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.5;">
                        <b style="color: #61afef;">🛡️ Bypass & Hooking Strategy for <code>{fname}</code>:</b><br/>
                        <ol style="margin: 4px 0 6px 18px; padding: 0;">
                            <li><b>Frida Interceptor Hook:</b>
                                <pre style="background-color: #14141c; padding: 8px; border-radius: 4px; color: #98c379; font-family: monospace; font-size: 11px; margin: 4px 0;">
Interceptor.attach(Module.findExportByName("libanogs.so", "{fname}") || ptr("{ea_str}"), {{
    onEnter: function(args) {{
        console.log("[*] {fname} security check entered");
    }},
    onLeave: function(retval) {{
        console.log("[*] {fname} Original return: " + retval);
        retval.replace(ptr(0));
    }}
}});
                                </pre>
                            </li>
                            <li><b>Binary Patch (ARM64):</b> Replace the first instruction at <code>{ea_str}</code> with <code>MOV W0, #0; RET</code> (Hex bytes: <code>00 00 80 52 C0 03 5F D6</code>).</li>
                        </ol>
                    </div>
                    """
                else:
                    return f"""
                    <div style="font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.5;">
                        <b style="color: #61afef;">🔧 Frida Script for <code>{fname}</code>:</b><br/>
                        <pre style="background-color: #14141c; padding: 8px; border-radius: 4px; color: #98c379; font-family: monospace; font-size: 11px; margin: 4px 0;">
Interceptor.attach(ptr("{ea_str}"), {{
    onEnter: function(args) {{
        console.log("[*] {fname} called with arg0=" + args[0] + ", arg1=" + args[1]);
    }},
    onLeave: function(retval) {{
        console.log("[*] {fname} returned: " + retval);
    }}
}});
                        </pre>
                    </div>
                    """

        elif any(w in q_lower for w in ["xref", "caller", "callers", "who calls", "siapa panggil", "dipanggil"]):
            if lang == "id":
                caller_items = "".join([f"<li><code>{c['name']}</code> pada alamat <code>{c['address']}</code></li>" for c in callers[:12]])
                return f"""
                <div style="font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.5;">
                    <b style="color: #61afef;">🔗 Analisis Referensi Silang (Pemanggil) untuk <code>{fname}</code>:</b><br/>
                    Fungsi ini dipanggil oleh <b>{len(callers)} lokasi</b> di dalam biner:<br/>
                    <ul style="margin: 6px 0 6px 18px; padding: 0; color: #abb2bf;">
                        {caller_items if caller_items else "<li>Tidak ditemukan pemanggil langsung (kemungkinan fungsi root, ekspor, atau dipanggil via pointer fungsi).</li>"}
                    </ul>
                </div>
                """
            else:
                caller_items = "".join([f"<li><code>{c['name']}</code> at address <code>{c['address']}</code></li>" for c in callers[:12]])
                return f"""
                <div style="font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.5;">
                    <b style="color: #61afef;">🔗 Cross-Reference Analysis (Callers) for <code>{fname}</code>:</b><br/>
                    This function is referenced by <b>{len(callers)} locations</b> in the binary:<br/>
                    <ul style="margin: 6px 0 6px 18px; padding: 0; color: #abb2bf;">
                        {caller_items if caller_items else "<li>No direct xref callers found (likely a root routine, export, or dynamically dispatched via function pointer).</li>"}
                    </ul>
                </div>
                """
        else:
            if lang == "id":
                return f"""
                <div style="font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.5;">
                    <b style="color: #61afef;">Analisis Fungsi {fname}:</b><br/>
                    Fungsi berada pada rentang <code>{ea_str}</code> berukuran {size} byte dengan klasifikasi <b>{sem['category']}</b>.<br/>
                    Kueri Anda <i>"{query}"</i> telah dicatat. Gunakan tombol <b>'Deobfuskasi && Jelaskan'</b> untuk ringkasan alur logika lengkap atau pilih Direct AI API untuk penalaran LLM mendalam.
                </div>
                """
            else:
                return f"""
                <div style="font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.5;">
                    <b style="color: #61afef;">Analysis for {fname}:</b><br/>
                    Function is located at <code>{ea_str}</code> (size: {size} bytes), classified as <b>{sem['category']}</b>.<br/>
                    Your query <i>"{query}"</i> is recorded. Use the <b>'Deobfuscate && Explain'</b> button for full execution logic or select Direct AI API for real-time LLM reasoning.
                </div>
                """

    @classmethod
    def on_ea_changed(cls, ea):
        if ea == cls.last_cursor_ea:
            return
        cls.last_cursor_ea = ea
        if cls.tab_instance and hasattr(cls.tab_instance, "refresh_cursor_info"):
            cls.tab_instance.refresh_cursor_info(ea)

DASHBOARD_QSS = """
QFrame#cardPanel {
    background-color: #11151D;
    border: 1px solid #283142;
    border-radius: 8px;
}

QTabWidget::pane {
    border: 1px solid #283142;
    background-color: #0B0E14;
    border-radius: 8px;
    top: -1px;
}

QTabBar::tab {
    background-color: #171D28;
    color: #CBD5E1;
    font-weight: 700;
    font-size: 11px;
    padding: 8px 18px;
    margin-right: 4px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    border: 1px solid #334155;
    border-bottom: none;
}

QTabBar::tab:selected {
    background-color: #0B0E14;
    color: #38BDF8;
    border-top: 2px solid #38BDF8;
    border-bottom: 1px solid #0B0E14;
}

QTabBar::tab:hover:!selected {
    background-color: #1E293B;
    color: #FFFFFF;
}

QPushButton {
    background-color: #1E293B;
    color: #F8FAFC;
    border: 1px solid #334155;
    border-radius: 5px;
    padding: 6px 14px;
    font-weight: 600;
    font-size: 11px;
}

QPushButton:hover {
    background-color: #334155;
    border-color: #475569;
}

QPushButton:pressed {
    background-color: #0F172A;
}

QPushButton#primaryAction {
    background-color: #6366F1;
    color: #FFFFFF;
    border: 1px solid #4F46E5;
}

QPushButton#primaryAction:hover {
    background-color: #4F46E5;
}

QPushButton#accentAction {
    background-color: #0284C7;
    color: #FFFFFF;
    border: 1px solid #0369A1;
}

QPushButton#accentAction:hover {
    background-color: #0369A1;
}

QPushButton#successAction {
    background-color: #059669;
    color: #FFFFFF;
    border: 1px solid #047857;
}

QPushButton#successAction:hover {
    background-color: #047857;
}

QLabel {
    color: #F8FAFC;
}

QLineEdit, QTextEdit {
    background-color: #11151D;
    border: 1px solid #334155;
    border-radius: 5px;
    padding: 6px 10px;
    color: #F8FAFC;
    font-size: 12px;
    selection-background-color: #0284C7;
    selection-color: #FFFFFF;
}

QLineEdit:focus, QTextEdit:focus {
    border: 1px solid #38BDF8;
    background-color: #171D28;
}

QComboBox {
    background-color: #11151D;
    border: 1px solid #334155;
    border-radius: 5px;
    padding: 5px 10px;
    color: #F8FAFC;
    font-size: 12px;
    font-weight: 500;
}

QComboBox:hover {
    border: 1px solid #475569;
}

QComboBox:focus {
    border: 1px solid #38BDF8;
    background-color: #171D28;
}

QComboBox:editable {
    background-color: #11151D;
    color: #F8FAFC;
}

QComboBox QLineEdit {
    color: #F8FAFC;
    background-color: transparent;
    border: none;
    padding: 0px;
    font-size: 12px;
    selection-background-color: #0284C7;
    selection-color: #FFFFFF;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 26px;
    border-left: 1px solid #334155;
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
    background-color: #1E293B;
}

QComboBox::drop-down:hover {
    background-color: #334155;
}

QComboBox::down-arrow {
    width: 0px;
    height: 0px;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #38BDF8;
}

QComboBox QAbstractItemView, QListView, QMenu {
    background-color: #0F172A;
    color: #F8FAFC;
    border: 1px solid #38BDF8;
    border-radius: 6px;
    padding: 4px;
    selection-background-color: #0284C7;
    selection-color: #FFFFFF;
    outline: none;
}

QComboBox QAbstractItemView::item, QListView::item, QComboBox::item {
    color: #F8FAFC;
    background-color: transparent;
    min-height: 28px;
    padding: 5px 10px;
    border-radius: 4px;
    font-size: 12px;
}

QComboBox QAbstractItemView::item:hover, QListView::item:hover, QComboBox::item:hover {
    background-color: #1E293B;
    color: #38BDF8;
}

QComboBox QAbstractItemView::item:selected, QListView::item:selected, QComboBox::item:selected {
    background-color: #0284C7;
    color: #FFFFFF;
    font-weight: 600;
}

QTableWidget {
    background-color: #11151D;
    alternate-background-color: #171D28;
    border: 1px solid #283142;
    border-radius: 6px;
    gridline-color: #1E293B;
    font-size: 11px;
    color: #F8FAFC;
}

QTableWidget::item:selected {
    background-color: #1E293B;
    color: #38BDF8;
}

QHeaderView::section {
    background-color: #171D28;
    color: #E2E8F0;
    font-weight: 700;
    font-size: 11px;
    padding: 6px;
    border: 1px solid #283142;
}

QListWidget {
    background-color: #171D28;
    border: 1px solid #283142;
    border-radius: 6px;
    color: #E8EDF5;
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 11px;
}

QListWidget::item {
    padding: 5px 8px;
    border-bottom: 1px solid #1F2737;
}

QListWidget::item:hover {
    background-color: #1E293B;
    color: #38BDF8;
}

QListWidget::item:selected {
    background-color: #283142;
    color: #38BDF8;
}

QScrollBar:vertical {
    background-color: #11151D;
    width: 8px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background-color: #334155;
    min-height: 20px;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background-color: #8B5CF6;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
    background: none;
}

QScrollBar:horizontal {
    background-color: #11151D;
    height: 8px;
    margin: 0px;
}

QScrollBar::handle:horizontal {
    background-color: #334155;
    min-width: 20px;
    border-radius: 4px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #8B5CF6;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
    background: none;
}

QToolTip {
    background-color: #171D28;
    color: #E8EDF5;
    border: 1px solid #38455B;
    border-radius: 4px;
    padding: 5px 8px;
    font-size: 11px;
}
"""

_StyledItemDelegateBase = QtWidgets.QStyledItemDelegate if QtWidgets else object

class BrightItemDelegate(_StyledItemDelegateBase):
    def initStyleOption(self, option, index):
        if hasattr(super(), "initStyleOption"):
            super().initStyleOption(option, index)
        if QtGui:
            option.palette.setColor(QtGui.QPalette.Text, QtGui.QColor("#F8FAFC"))
            option.palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#F8FAFC"))
            option.palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#FFFFFF"))

class McpDashboardTab(ida_kernwin.PluginForm):
    def configure_bright_combo(self, combo):
        if not combo or not QtWidgets:
            return
        view = QtWidgets.QListView()
        if QtGui:
            p = view.palette()
            bright = QtGui.QColor("#F8FAFC")
            bg = QtGui.QColor("#0F172A")
            sel_bg = QtGui.QColor("#0284C7")
            sel_fg = QtGui.QColor("#FFFFFF")
            p.setColor(QtGui.QPalette.Text, bright)
            p.setColor(QtGui.QPalette.WindowText, bright)
            p.setColor(QtGui.QPalette.Base, bg)
            p.setColor(QtGui.QPalette.Window, bg)
            p.setColor(QtGui.QPalette.Highlight, sel_bg)
            p.setColor(QtGui.QPalette.HighlightedText, sel_fg)
            view.setPalette(p)
            cp = combo.palette()
            cp.setColor(QtGui.QPalette.Text, bright)
            cp.setColor(QtGui.QPalette.WindowText, bright)
            cp.setColor(QtGui.QPalette.ButtonText, bright)
            combo.setPalette(cp)
        view.setStyleSheet(
            "QListView { background-color: #0F172A; color: #F8FAFC; selection-background-color: #0284C7; selection-color: #FFFFFF; font-size: 12px; border: 1px solid #38BDF8; outline: none; } "
            "QListView::item { min-height: 26px; padding: 4px 8px; color: #F8FAFC; background-color: transparent; } "
            "QListView::item:hover { background-color: #1E293B; color: #38BDF8; } "
            "QListView::item:selected { background-color: #0284C7; color: #FFFFFF; font-weight: 600; }"
        )
        try:
            view.setItemDelegate(BrightItemDelegate(view))
        except Exception:
            pass
        combo.setView(view)
        le = combo.lineEdit()
        if le:
            le.setStyleSheet("background-color: transparent; color: #F8FAFC; font-size: 12px; border: none;")
            if QtGui:
                lep = le.palette()
                lep.setColor(QtGui.QPalette.Text, QtGui.QColor("#F8FAFC"))
                lep.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#F8FAFC"))
                le.setPalette(lep)

    def __init__(self):
        super().__init__()
        self.parent = None
        self.title_lbl = None
        self.bin_lbl = None
        self.lang_combo = None
        self.agent_pill = None
        self.status_pill = None
        self.refresh_btn = None
        self.tabs = None

        self.s_title = None
        self.copy_url_btn = None
        self.test_conn_btn = None
        self.endpoint_badge = None
        self.proto_badge = None
        self.tools_badge = None
        self.reqs_badge = None
        self.conn_feedback_lbl = None

        self.a_title = None
        self.a_sub = None
        self.sel_agent_lbl = None
        self.agent_combo = None
        self.agent_desc_box = None
        self.custom_agent_panel = None
        self.copy_agent_cfg_btn = None
        self.save_agent_cfg_btn = None
        self.run_local_agent_btn = None
        self.agent_feedback_lbl = None

        self.t_title = None
        self.tools_count_lbl = None
        self.cat_lbl = None
        self.category_combo = None
        self.tool_search = None
        self.tools_table = None

        self.func_title_lbl = None
        self.cff_badge = None
        self.copilot_agent_badge = None
        self.copilot_model_badge = None
        self.jump_btn = None
        self.target_func_lbl = None
        self.func_combo = None
        self.func_meta_lbl = None
        self.btn_deobf = None
        self.btn_sugg = None
        self.btn_apply = None
        self.btn_export = None
        self.ai_output_view = None
        self.prompt_input = None
        self.ask_btn = None

        self.copy_c_btn = None
        self.save_c_btn = None
        self.c_status_lbl = None
        self.code_viewer = None

        self.callers_header = None
        self.callers_list = None
        self.callees_header = None
        self.callees_list = None

        self.log_agent_lbl = None
        self.filter_lbl = None
        self.log_filter_combo = None
        self.clear_log_btn = None
        self.log_text = None
        self.active_log_filter = "ALL"

        self.p_title = None
        self.custom_agent_badge = None
        self.lbl_prov = None
        self.provider_combo = None
        self.lbl_url = None
        self.base_url_input = None
        self.lbl_key = None
        self.api_key_input = None
        self.toggle_key_btn = None
        self.lbl_model = None
        self.model_combo = None
        self.models_count_lbl = None
        self.fetch_models_btn = None
        self.test_provider_conn_btn = None
        self.test_model_btn = None
        self.save_custom_agent_btn = None
        self.fetch_status_lbl = None
        self.model_meta_lbl = None
        self.custom_agent_status_lbl = None

        self.auto_fetch_timer = None
        self._fetch_seq = 0
        self._active_key_visible = False

    def OnCreate(self, form):
        try:
            parent = None
            if hasattr(self, "FormToPyQtWidget"):
                try:
                    parent = self.FormToPyQtWidget(form)
                except Exception:
                    pass
            if parent is None and hasattr(self, "FormToPySideWidget"):
                try:
                    parent = self.FormToPySideWidget(form)
                except Exception:
                    pass

            self.parent = parent
            if "McpDashboardState" in globals():
                McpDashboardState.load_agent_config()
            self.init_ui()
            self.apply_language(McpDashboardState.get_language())
            self.refresh_all()
        except Exception as e:
            import traceback
            print(f"[MCP Dashboard] Error in OnCreate: {e}")
            traceback.print_exc()

    def init_ui(self):
        if not self.parent or not QtWidgets:
            return

        self.parent.setStyleSheet(DASHBOARD_QSS)

        root_layout = QtWidgets.QVBoxLayout()
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        header_container = QtWidgets.QFrame()
        header_container.setStyleSheet("background-color: #11151D; border: 1px solid #283142; border-radius: 8px; padding: 4px 8px;")
        header_bar = QtWidgets.QHBoxLayout(header_container)
        header_bar.setContentsMargins(4, 4, 4, 4)
        header_bar.setSpacing(10)

        self.title_lbl = QtWidgets.QLabel(McpDashboardState.t("title"))
        self.title_lbl.setStyleSheet("font-size: 13px; font-weight: 800; color: #8B5CF6; letter-spacing: 0.8px; padding: 2px 6px; background-color: #171D28; border: 1px solid #283142; border-radius: 4px;")
        header_bar.addWidget(self.title_lbl)

        self.bin_lbl = QtWidgets.QLabel("libanogs.so (ARM64) | 5372 " + McpDashboardState.t("funcs_suffix"))
        self.bin_lbl.setStyleSheet("color: #E2E8F0; font-size: 12px; font-weight: 600; padding: 2px 8px; background-color: #171D28; border: 1px solid #283142; border-radius: 4px;")
        header_bar.addWidget(self.bin_lbl)

        header_bar.addStretch()

        self.lang_combo = QtWidgets.QComboBox()
        self.configure_bright_combo(self.lang_combo)
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("Bahasa Indonesia", "id")
        cur_lang = McpDashboardState.get_language()
        l_idx = self.lang_combo.findData(cur_lang)
        if l_idx >= 0:
            self.lang_combo.setCurrentIndex(l_idx)
        self.lang_combo.setStyleSheet("background-color: #171D28; color: #F1F5F9; border: 1px solid #38455B; border-radius: 6px; padding: 3px 8px; font-size: 11px; font-weight: 600;")
        self.lang_combo.currentIndexChanged.connect(self.on_lang_combo_changed)
        header_bar.addWidget(self.lang_combo)

        active_agent = "Antigravity"
        if "McpDashboardState" in globals():
            active_agent = McpDashboardState.get_active_agent_key()
        self.agent_pill = QtWidgets.QLabel(f"{McpDashboardState.t('agent_prefix')} {active_agent}")
        self.agent_pill.setStyleSheet("background-color: #171D28; color: #38BDF8; border: 1px solid #38BDF8; border-radius: 12px; padding: 3px 12px; font-size: 11px; font-weight: 600;")
        header_bar.addWidget(self.agent_pill)

        self.status_pill = QtWidgets.QLabel(McpDashboardState.t("mcp_online"))
        self.status_pill.setStyleSheet("background-color: #064E3B; color: #34D399; border: 1px solid #34D399; border-radius: 12px; padding: 3px 12px; font-size: 11px; font-weight: 600;")
        header_bar.addWidget(self.status_pill)

        self.refresh_btn = QtWidgets.QPushButton(McpDashboardState.t("refresh"))
        self.refresh_btn.setToolTip(McpDashboardState.t("refresh_tooltip"))
        self.refresh_btn.clicked.connect(self.on_refresh_clicked)
        header_bar.addWidget(self.refresh_btn)

        root_layout.addWidget(header_container)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setFocusPolicy(QtCore.Qt.TabFocus)

        tab_mcp = self.create_mcp_dashboard_tab()
        self.tabs.addTab(tab_mcp, McpDashboardState.t("tab_mcp"))

        tab_ai = self.create_copilot_tab()
        self.tabs.addTab(tab_ai, McpDashboardState.t("tab_copilot"))

        tab_code = self.create_decompiled_c_tab()
        self.tabs.addTab(tab_code, McpDashboardState.t("tab_decompiled"))

        tab_xrefs = self.create_xrefs_tab()
        self.tabs.addTab(tab_xrefs, McpDashboardState.t("tab_xrefs"))

        tab_log = self.create_log_tab()
        self.tabs.addTab(tab_log, McpDashboardState.t("tab_log"))

        root_layout.addWidget(self.tabs)
        self.parent.setLayout(root_layout)

    def on_lang_combo_changed(self, idx):
        if idx < 0 or not self.lang_combo:
            return
        lang = self.lang_combo.itemData(idx)
        if lang and lang in ("en", "id"):
            McpDashboardState.set_language(lang)

    def apply_language(self, lang):
        if not QtWidgets or not self.parent:
            return
        t = lambda k, **kw: McpDashboardState.t(k, **kw)
        if self.title_lbl:
            self.title_lbl.setText(t("title"))
        if self.tabs:
            self.tabs.setTabText(0, t("tab_mcp"))
            self.tabs.setTabText(1, t("tab_copilot"))
            self.tabs.setTabText(2, t("tab_decompiled"))
            self.tabs.setTabText(3, t("tab_xrefs"))
            self.tabs.setTabText(4, t("tab_log"))
        if self.refresh_btn and self.refresh_btn.isEnabled():
            self.refresh_btn.setText(t("refresh"))
            self.refresh_btn.setToolTip(t("refresh_tooltip"))
        if self.s_title:
            self.s_title.setText(t("server_status_title"))
        if self.copy_url_btn:
            self.copy_url_btn.setText(t("copy_mcp_url"))
        if self.test_conn_btn and self.test_conn_btn.isEnabled():
            self.test_conn_btn.setText(t("test_connection"))
        if self.proto_badge:
            self.proto_badge.setText(t("proto_badge"))
        if self.tools_badge:
            self.tools_badge.setText(t("tools_badge"))
        if self.a_title:
            self.a_title.setText(t("user_agent_cfg_title"))
        if self.a_sub:
            self.a_sub.setText(t("user_agent_cfg_sub"))
        if self.sel_agent_lbl:
            self.sel_agent_lbl.setText(t("select_agent_lbl"))
        if self.copy_agent_cfg_btn:
            self.copy_agent_cfg_btn.setText(t("copy_agent_cfg"))
        if self.save_agent_cfg_btn:
            self.save_agent_cfg_btn.setText(t("save_agent_pref"))
        if self.run_local_agent_btn:
            self.run_local_agent_btn.setText(t("run_local_agent"))
        if self.t_title:
            self.t_title.setText(t("tools_catalog_title"))
        if self.cat_lbl:
            self.cat_lbl.setText(t("category_lbl"))
        if self.category_combo and self.category_combo.count() >= 8:
            self.category_combo.setItemText(0, t("all_categories"))
            self.category_combo.setItemText(1, t("cat_nav"))
            self.category_combo.setItemText(2, t("cat_calls"))
            self.category_combo.setItemText(3, t("cat_mod"))
            self.category_combo.setItemText(4, t("cat_stack"))
            self.category_combo.setItemText(5, t("cat_struct"))
            self.category_combo.setItemText(6, t("cat_mem"))
            self.category_combo.setItemText(7, t("cat_dbg"))
        if self.tool_search:
            self.tool_search.setPlaceholderText(t("tools_filter_placeholder"))
        if self.tools_table:
            self.tools_table.setHorizontalHeaderLabels([
                t("col_no"),
                t("col_method_name"),
                t("col_category"),
                t("col_calls"),
                t("col_desc")
            ])
        if self.cff_badge:
            self.cff_badge.setText(t("cff_badge"))
        if self.jump_btn:
            self.jump_btn.setText(t("jump_in_ida"))
        if self.target_func_lbl:
            self.target_func_lbl.setText(t("target_func_lbl"))
        if self.btn_deobf:
            self.btn_deobf.setText(t("btn_deobfuscate"))
        if self.btn_sugg:
            self.btn_sugg.setText(t("btn_suggest_renames"))
        if self.btn_apply:
            self.btn_apply.setText(t("btn_apply_renames"))
        active_agent = McpDashboardState.get_active_agent_key()
        if self.agent_pill:
            self.agent_pill.setText(f"{t('agent_prefix')} {active_agent}")
        if self.btn_export:
            self.btn_export.setText(t("btn_send_to_agent", agent=active_agent))
        if self.ai_output_view and not self.ai_output_view.toPlainText().strip():
            self.ai_output_view.setPlaceholderText(t("ai_placeholder"))
        if self.prompt_input:
            self.prompt_input.setPlaceholderText(t("ask_placeholder", agent=active_agent))
        if self.ask_btn:
            self.ask_btn.setText(t("ask_btn", agent=active_agent))
        if self.copy_c_btn:
            self.copy_c_btn.setText(t("copy_c_code"))
        if self.save_c_btn:
            self.save_c_btn.setText(t("save_c_file"))
        if self.log_agent_lbl:
            self.log_agent_lbl.setText(t("log_stream_title", agent=active_agent))
        if self.filter_lbl:
            self.filter_lbl.setText(t("filter_event_lbl"))
        if self.log_filter_combo and self.log_filter_combo.count() >= 5:
            self.log_filter_combo.setItemText(0, t("all_events"))
            self.log_filter_combo.setItemText(1, t("agent_calls"))
            self.log_filter_combo.setItemText(2, t("agent_success"))
            self.log_filter_combo.setItemText(3, t("errors"))
            self.log_filter_combo.setItemText(4, t("ui_actions"))
        if self.clear_log_btn:
            self.clear_log_btn.setText(t("clear_log"))
        if self.p_title:
            self.p_title.setText(t("custom_agent_title"))
        if self.custom_agent_badge:
            self.custom_agent_badge.setText(t("direct_llm_mode"))
        if self.lbl_prov:
            self.lbl_prov.setText(t("provider_name_lbl"))
        if self.lbl_url:
            self.lbl_url.setText(t("base_url_lbl"))
        if self.lbl_key:
            self.lbl_key.setText(t("api_key_lbl"))
        if self.api_key_input:
            self.api_key_input.setPlaceholderText(t("api_key_placeholder"))
        if self.toggle_key_btn:
            self.toggle_key_btn.setText(t("hide_key") if self._active_key_visible else t("show_key"))
        if self.lbl_model:
            self.lbl_model.setText(t("model_id_lbl"))
        if self.model_combo:
            self.model_combo.setToolTip(t("model_combo_tooltip"))
        if self.fetch_models_btn and self.fetch_models_btn.isEnabled():
            self.fetch_models_btn.setText(t("fetch_models"))
        if self.test_provider_conn_btn and self.test_provider_conn_btn.isEnabled():
            self.test_provider_conn_btn.setText(t("test_connection"))
        if self.test_model_btn and self.test_model_btn.isEnabled():
            self.test_model_btn.setText(t("test_selected_model"))
        if self.save_custom_agent_btn:
            self.save_custom_agent_btn.setText(t("save_agent_config"))
        self.update_agent_view()
        self.refresh_all()

    def create_mcp_dashboard_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        card_server = QtWidgets.QFrame()
        card_server.setObjectName("cardPanel")
        srv_layout = QtWidgets.QVBoxLayout(card_server)
        srv_layout.setContentsMargins(12, 10, 12, 10)
        srv_layout.setSpacing(10)

        s_top = QtWidgets.QHBoxLayout()
        self.s_title = QtWidgets.QLabel(McpDashboardState.t("server_status_title"))
        self.s_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #E8EDF5;")
        s_top.addWidget(self.s_title)
        s_top.addStretch()

        self.copy_url_btn = QtWidgets.QPushButton(McpDashboardState.t("copy_mcp_url"))
        self.copy_url_btn.clicked.connect(self.on_copy_mcp_url)
        s_top.addWidget(self.copy_url_btn)

        self.test_conn_btn = QtWidgets.QPushButton(McpDashboardState.t("test_connection"))
        self.test_conn_btn.setObjectName("accentAction")
        self.test_conn_btn.clicked.connect(self.on_test_mcp_clicked)
        s_top.addWidget(self.test_conn_btn)
        srv_layout.addLayout(s_top)

        metrics_row = QtWidgets.QHBoxLayout()
        metrics_row.setSpacing(8)

        self.endpoint_badge = QtWidgets.QLabel("Endpoint: http://127.0.0.1:13337/mcp")
        self.endpoint_badge.setStyleSheet("background-color: #171D28; border: 1px solid #283142; border-radius: 4px; padding: 5px 10px; color: #38BDF8; font-family: monospace; font-size: 11px;")
        metrics_row.addWidget(self.endpoint_badge)

        self.proto_badge = QtWidgets.QLabel(McpDashboardState.t("proto_badge"))
        self.proto_badge.setStyleSheet("background-color: #171D28; border: 1px solid #283142; border-radius: 4px; padding: 5px 10px; color: #CBD5E1; font-weight: 600; font-size: 11px;")
        metrics_row.addWidget(self.proto_badge)

        self.tools_badge = QtWidgets.QLabel(McpDashboardState.t("tools_badge"))
        self.tools_badge.setStyleSheet("background-color: #171D28; border: 1px solid #283142; border-radius: 4px; padding: 5px 10px; color: #CBD5E1; font-weight: 600; font-size: 11px;")
        metrics_row.addWidget(self.tools_badge)

        self.reqs_badge = QtWidgets.QLabel(McpDashboardState.t("reqs_badge", count=0))
        self.reqs_badge.setStyleSheet("background-color: #171D28; border: 1px solid #283142; border-radius: 4px; padding: 5px 10px; color: #34D399; font-weight: 600; font-size: 11px;")
        metrics_row.addWidget(self.reqs_badge)

        metrics_row.addStretch()
        srv_layout.addLayout(metrics_row)

        self.conn_feedback_lbl = QtWidgets.QLabel("")
        self.conn_feedback_lbl.setVisible(False)
        self.conn_feedback_lbl.setStyleSheet("border-radius: 4px; padding: 4px 8px; font-size: 11px; font-weight: 600;")
        srv_layout.addWidget(self.conn_feedback_lbl)

        layout.addWidget(card_server)

        card_agent = QtWidgets.QFrame()
        card_agent.setObjectName("cardPanel")
        agent_layout = QtWidgets.QVBoxLayout(card_agent)
        agent_layout.setContentsMargins(12, 10, 12, 10)
        agent_layout.setSpacing(8)

        a_top = QtWidgets.QHBoxLayout()
        self.a_title = QtWidgets.QLabel(McpDashboardState.t("user_agent_cfg_title"))
        self.a_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #8B5CF6;")
        a_top.addWidget(self.a_title)

        self.a_sub = QtWidgets.QLabel(McpDashboardState.t("user_agent_cfg_sub"))
        self.a_sub.setStyleSheet("font-size: 11px; color: #CBD5E1; font-weight: 500;")
        a_top.addWidget(self.a_sub)
        a_top.addStretch()
        agent_layout.addLayout(a_top)

        sel_row = QtWidgets.QHBoxLayout()
        self.sel_agent_lbl = QtWidgets.QLabel(McpDashboardState.t("select_agent_lbl"))
        self.sel_agent_lbl.setStyleSheet("font-weight: 600; color: #E8EDF5;")
        sel_row.addWidget(self.sel_agent_lbl)

        self.agent_combo = QtWidgets.QComboBox()
        self.configure_bright_combo(self.agent_combo)
        self.agent_combo.addItem("Antigravity Agent (Google DeepMind)", "Antigravity")
        self.agent_combo.addItem("Claude Desktop / Claude Code", "Claude")
        self.agent_combo.addItem("Cursor IDE / Windsurf", "Cursor")
        self.agent_combo.addItem("Direct AI API (Gemini / OpenAI / DeepSeek / Ollama)", "Direct_API")
        self.agent_combo.addItem("Autonomous Script (ida_mcp_agent)", "Local_Script")

        cur_agent = "Antigravity"
        if "McpDashboardState" in globals():
            cur_agent = McpDashboardState.get_active_agent_key()
        idx = self.agent_combo.findData(cur_agent)
        if idx >= 0:
            self.agent_combo.setCurrentIndex(idx)
        self.agent_combo.currentIndexChanged.connect(self.on_agent_combo_changed)
        sel_row.addWidget(self.agent_combo)
        sel_row.addStretch()
        agent_layout.addLayout(sel_row)

        self.agent_desc_box = QtWidgets.QTextEdit()
        self.agent_desc_box.setReadOnly(True)
        self.agent_desc_box.setMaximumHeight(130)
        self.agent_desc_box.setStyleSheet("background-color: #171D28; border: 1px solid #283142; border-radius: 6px; padding: 8px; font-size: 12px; color: #E8EDF5;")
        agent_layout.addWidget(self.agent_desc_box)

        self.custom_agent_panel = self.create_custom_agent_panel()
        self.custom_agent_panel.setVisible(False)
        agent_layout.addWidget(self.custom_agent_panel)

        agent_btn_row = QtWidgets.QHBoxLayout()
        self.copy_agent_cfg_btn = QtWidgets.QPushButton(McpDashboardState.t("copy_agent_cfg"))
        self.copy_agent_cfg_btn.clicked.connect(self.on_copy_agent_config)
        agent_btn_row.addWidget(self.copy_agent_cfg_btn)

        self.save_agent_cfg_btn = QtWidgets.QPushButton(McpDashboardState.t("save_agent_pref"))
        self.save_agent_cfg_btn.clicked.connect(self.on_save_agent_preference)
        agent_btn_row.addWidget(self.save_agent_cfg_btn)

        self.run_local_agent_btn = QtWidgets.QPushButton(McpDashboardState.t("run_local_agent"))
        self.run_local_agent_btn.setObjectName("primaryAction")
        self.run_local_agent_btn.clicked.connect(self.on_run_local_agent)
        agent_btn_row.addWidget(self.run_local_agent_btn)

        self.agent_feedback_lbl = QtWidgets.QLabel("")
        self.agent_feedback_lbl.setStyleSheet("color: #34D399; font-size: 11px; font-weight: 600; padding-left: 8px;")
        agent_btn_row.addWidget(self.agent_feedback_lbl)

        agent_btn_row.addStretch()
        agent_layout.addLayout(agent_btn_row)

        layout.addWidget(card_agent)

        tools_card = QtWidgets.QFrame()
        tools_card.setObjectName("cardPanel")
        t_layout = QtWidgets.QVBoxLayout(tools_card)
        t_layout.setContentsMargins(12, 10, 12, 10)
        t_layout.setSpacing(8)

        t_top = QtWidgets.QHBoxLayout()
        self.t_title = QtWidgets.QLabel(McpDashboardState.t("tools_catalog_title"))
        self.t_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #E8EDF5;")
        t_top.addWidget(self.t_title)

        self.tools_count_lbl = QtWidgets.QLabel(McpDashboardState.t("tools_registered_count", count=59))
        self.tools_count_lbl.setStyleSheet("font-size: 11px; color: #CBD5E1; font-weight: 600;")
        t_top.addWidget(self.tools_count_lbl)
        t_top.addStretch()

        self.cat_lbl = QtWidgets.QLabel(McpDashboardState.t("category_lbl"))
        self.cat_lbl.setStyleSheet("color: #F8FAFC; font-size: 12px; font-weight: 600;")
        t_top.addWidget(self.cat_lbl)

        self.category_combo = QtWidgets.QComboBox()
        self.configure_bright_combo(self.category_combo)
        self.category_combo.addItem(McpDashboardState.t("all_categories"), "")
        self.category_combo.addItem(McpDashboardState.t("cat_nav"), "Navigation")
        self.category_combo.addItem(McpDashboardState.t("cat_calls"), "Calls")
        self.category_combo.addItem(McpDashboardState.t("cat_mod"), "Modification")
        self.category_combo.addItem(McpDashboardState.t("cat_stack"), "Stack")
        self.category_combo.addItem(McpDashboardState.t("cat_struct"), "Structures")
        self.category_combo.addItem(McpDashboardState.t("cat_mem"), "Memory")
        self.category_combo.addItem(McpDashboardState.t("cat_dbg"), "Debugger")
        self.category_combo.currentIndexChanged.connect(self.filter_tools_table)
        t_top.addWidget(self.category_combo)

        self.tool_search = QtWidgets.QLineEdit()
        self.tool_search.setPlaceholderText(McpDashboardState.t("tools_filter_placeholder"))
        self.tool_search.textChanged.connect(self.filter_tools_table)
        self.tool_search.setMinimumWidth(260)
        t_top.addWidget(self.tool_search)
        t_layout.addLayout(t_top)

        self.tools_table = QtWidgets.QTableWidget()
        self.tools_table.setColumnCount(5)
        self.tools_table.setHorizontalHeaderLabels([
            McpDashboardState.t("col_no"),
            McpDashboardState.t("col_method_name"),
            McpDashboardState.t("col_category"),
            McpDashboardState.t("col_calls"),
            McpDashboardState.t("col_desc")
        ])
        self.tools_table.horizontalHeader().setStretchLastSection(True)
        self.tools_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tools_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tools_table.setAlternatingRowColors(True)
        self.tools_table.verticalHeader().setVisible(False)
        self.tools_table.verticalHeader().setDefaultSectionSize(32)
        self.populate_tools_table()
        t_layout.addWidget(self.tools_table)

        layout.addWidget(tools_card)
        self.update_agent_view()
        return w

    def create_copilot_tab(self):
        tab_ai = QtWidgets.QWidget()
        ai_layout = QtWidgets.QVBoxLayout(tab_ai)
        ai_layout.setContentsMargins(8, 8, 8, 8)
        ai_layout.setSpacing(8)

        card = QtWidgets.QFrame()
        card.setObjectName("cardPanel")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(8)

        c_top = QtWidgets.QHBoxLayout()
        self.func_title_lbl = QtWidgets.QLabel(McpDashboardState.t("func_title_loading"))
        self.func_title_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #E8EDF5;")
        c_top.addWidget(self.func_title_lbl)

        self.cff_badge = QtWidgets.QLabel(McpDashboardState.t("cff_badge"))
        self.cff_badge.setStyleSheet("background-color: #78350F; color: #FBBF24; border: 1px solid #FBBF24; border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 8px;")
        self.cff_badge.setVisible(False)
        c_top.addWidget(self.cff_badge)

        self.copilot_agent_badge = QtWidgets.QLabel(f"{McpDashboardState.t('agent_prefix')} Antigravity")
        self.copilot_agent_badge.setStyleSheet("background-color: #1E293B; color: #38BDF8; border: 1px solid #334155; border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 8px;")
        c_top.addWidget(self.copilot_agent_badge)

        self.copilot_model_badge = QtWidgets.QLabel("Model: default")
        self.copilot_model_badge.setStyleSheet("background-color: #1E293B; color: #A78BFA; border: 1px solid #4C1D95; border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 8px;")
        self.copilot_model_badge.setVisible(False)
        c_top.addWidget(self.copilot_model_badge)

        c_top.addStretch()

        self.jump_btn = QtWidgets.QPushButton(McpDashboardState.t("jump_in_ida"))
        self.jump_btn.setMaximumWidth(120)
        self.jump_btn.clicked.connect(self.on_jump_to_current)
        c_top.addWidget(self.jump_btn)
        card_layout.addLayout(c_top)

        sel_row = QtWidgets.QHBoxLayout()
        self.target_func_lbl = QtWidgets.QLabel(McpDashboardState.t("target_func_lbl"))
        sel_row.addWidget(self.target_func_lbl)
        self.func_combo = QtWidgets.QComboBox()
        self.func_combo.setEditable(True)
        self.configure_bright_combo(self.func_combo)
        self.func_combo.setMinimumWidth(320)
        self.func_combo.currentIndexChanged.connect(self.on_combo_func_selected)
        sel_row.addWidget(self.func_combo)

        self.func_meta_lbl = QtWidgets.QLabel("")
        self.func_meta_lbl.setStyleSheet("color: #CBD5E1; font-size: 12px; font-family: monospace; font-weight: 500;")
        sel_row.addWidget(self.func_meta_lbl)
        sel_row.addStretch()
        card_layout.addLayout(sel_row)
        ai_layout.addWidget(card)

        btn_row = QtWidgets.QHBoxLayout()
        self.btn_deobf = QtWidgets.QPushButton(McpDashboardState.t("btn_deobfuscate"))
        self.btn_deobf.setObjectName("primaryAction")
        self.btn_deobf.clicked.connect(self.on_deobfuscate_clicked)
        btn_row.addWidget(self.btn_deobf)

        self.btn_sugg = QtWidgets.QPushButton(McpDashboardState.t("btn_suggest_renames"))
        self.btn_sugg.clicked.connect(self.on_suggest_renames_clicked)
        btn_row.addWidget(self.btn_sugg)

        self.btn_apply = QtWidgets.QPushButton(McpDashboardState.t("btn_apply_renames"))
        self.btn_apply.setObjectName("successAction")
        self.btn_apply.clicked.connect(self.on_apply_renames_clicked)
        btn_row.addWidget(self.btn_apply)

        active_agent = "Antigravity"
        if "McpDashboardState" in globals():
            active_agent = McpDashboardState.get_active_agent_key()
        self.btn_export = QtWidgets.QPushButton(McpDashboardState.t("btn_send_to_agent", agent=active_agent))
        self.btn_export.setObjectName("accentAction")
        self.btn_export.clicked.connect(self.on_send_func)
        btn_row.addWidget(self.btn_export)
        ai_layout.addLayout(btn_row)

        self.ai_output_view = QtWidgets.QTextEdit()
        self.ai_output_view.setReadOnly(True)
        self.ai_output_view.setPlaceholderText(McpDashboardState.t("ai_placeholder"))
        ai_layout.addWidget(self.ai_output_view)

        prompt_box = QtWidgets.QHBoxLayout()
        self.prompt_input = QtWidgets.QLineEdit()
        self.prompt_input.setPlaceholderText(McpDashboardState.t("ask_placeholder", agent=active_agent))
        self.prompt_input.returnPressed.connect(self.on_submit_prompt)
        prompt_box.addWidget(self.prompt_input)

        self.ask_btn = QtWidgets.QPushButton(McpDashboardState.t("ask_btn", agent=active_agent))
        self.ask_btn.setObjectName("primaryAction")
        self.ask_btn.clicked.connect(self.on_submit_prompt)
        prompt_box.addWidget(self.ask_btn)
        ai_layout.addLayout(prompt_box)

        return tab_ai

    def create_decompiled_c_tab(self):
        tab_code = QtWidgets.QWidget()
        code_layout = QtWidgets.QVBoxLayout(tab_code)
        code_layout.setContentsMargins(8, 8, 8, 8)
        code_layout.setSpacing(8)

        c_toolbar = QtWidgets.QHBoxLayout()
        self.copy_c_btn = QtWidgets.QPushButton(McpDashboardState.t("copy_c_code"))
        self.copy_c_btn.clicked.connect(self.on_copy_c_code)
        c_toolbar.addWidget(self.copy_c_btn)

        self.save_c_btn = QtWidgets.QPushButton(McpDashboardState.t("save_c_file"))
        self.save_c_btn.clicked.connect(self.on_send_func)
        c_toolbar.addWidget(self.save_c_btn)

        self.c_status_lbl = QtWidgets.QLabel("")
        self.c_status_lbl.setStyleSheet("color: #34D399; font-size: 11px; font-weight: 600; padding-left: 8px;")
        c_toolbar.addWidget(self.c_status_lbl)

        c_toolbar.addStretch()
        code_layout.addLayout(c_toolbar)

        self.code_viewer = QtWidgets.QTextEdit()
        self.code_viewer.setReadOnly(True)
        self.code_viewer.setStyleSheet("background-color: #171D28; border: 1px solid #283142; border-radius: 6px; font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 12px; color: #E8EDF5; line-height: 140%;")
        code_layout.addWidget(self.code_viewer)
        return tab_code

    def create_xrefs_tab(self):
        tab_xrefs = QtWidgets.QWidget()
        xrefs_layout = QtWidgets.QHBoxLayout(tab_xrefs)
        xrefs_layout.setContentsMargins(8, 8, 8, 8)
        xrefs_layout.setSpacing(10)

        col1_frame = QtWidgets.QFrame()
        col1_frame.setObjectName("cardPanel")
        col1 = QtWidgets.QVBoxLayout(col1_frame)
        self.callers_header = QtWidgets.QLabel(McpDashboardState.t("callers_header", count=0))
        self.callers_header.setStyleSheet("font-weight: 700; color: #38BDF8; font-size: 12px;")
        col1.addWidget(self.callers_header)
        self.callers_list = QtWidgets.QListWidget()
        self.callers_list.itemDoubleClicked.connect(self.on_xref_item_clicked)
        col1.addWidget(self.callers_list)
        xrefs_layout.addWidget(col1_frame)

        col2_frame = QtWidgets.QFrame()
        col2_frame.setObjectName("cardPanel")
        col2 = QtWidgets.QVBoxLayout(col2_frame)
        self.callees_header = QtWidgets.QLabel(McpDashboardState.t("callees_header", count=0))
        self.callees_header.setStyleSheet("font-weight: 700; color: #8B5CF6; font-size: 12px;")
        col2.addWidget(self.callees_header)
        self.callees_list = QtWidgets.QListWidget()
        self.callees_list.itemDoubleClicked.connect(self.on_xref_item_clicked)
        col2.addWidget(self.callees_list)
        xrefs_layout.addWidget(col2_frame)

        return tab_xrefs

    def create_log_tab(self):
        tab_log = QtWidgets.QWidget()
        log_layout = QtWidgets.QVBoxLayout(tab_log)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_layout.setSpacing(8)

        log_top = QtWidgets.QHBoxLayout()
        active_agent = "Antigravity"
        if "McpDashboardState" in globals():
            active_agent = McpDashboardState.get_active_agent_key()
        self.log_agent_lbl = QtWidgets.QLabel(McpDashboardState.t("log_stream_title", agent=active_agent))
        self.log_agent_lbl.setStyleSheet("font-weight: 600; color: #38BDF8; font-size: 12px;")
        log_top.addWidget(self.log_agent_lbl)
        log_top.addStretch()

        self.filter_lbl = QtWidgets.QLabel(McpDashboardState.t("filter_event_lbl"))
        self.filter_lbl.setStyleSheet("color: #F8FAFC; font-size: 12px; font-weight: 600;")
        log_top.addWidget(self.filter_lbl)

        self.log_filter_combo = QtWidgets.QComboBox()
        self.configure_bright_combo(self.log_filter_combo)
        self.log_filter_combo.addItem(McpDashboardState.t("all_events"), "ALL")
        self.log_filter_combo.addItem(McpDashboardState.t("agent_calls"), "agent_call")
        self.log_filter_combo.addItem(McpDashboardState.t("agent_success"), "agent_success")
        self.log_filter_combo.addItem(McpDashboardState.t("errors"), "agent_error")
        self.log_filter_combo.addItem(McpDashboardState.t("ui_actions"), "ui_action")
        self.log_filter_combo.currentIndexChanged.connect(self.on_log_filter_changed)
        log_top.addWidget(self.log_filter_combo)

        self.clear_log_btn = QtWidgets.QPushButton(McpDashboardState.t("clear_log"))
        self.clear_log_btn.clicked.connect(self.on_clear_log)
        log_top.addWidget(self.clear_log_btn)
        log_layout.addLayout(log_top)

        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("background-color: #11151D; border: 1px solid #283142; border-radius: 6px; font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 12px; padding: 8px;")
        log_layout.addWidget(self.log_text)
        return tab_log

    def on_refresh_clicked(self):
        if self.refresh_btn:
            self.refresh_btn.setEnabled(False)
            self.refresh_btn.setText(McpDashboardState.t("refreshing"))
        if self.test_conn_btn and not self.test_conn_btn.isEnabled():
            self.test_conn_btn.setEnabled(True)
            self.test_conn_btn.setText(McpDashboardState.t("test_connection"))
        self.refresh_all()
        if QtCore:
            QtCore.QTimer.singleShot(400, self.restore_refresh_btn)

    def restore_refresh_btn(self):
        if self.refresh_btn:
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText(McpDashboardState.t("refresh"))

    def on_copy_mcp_url(self):
        if QtWidgets:
            QtWidgets.QApplication.clipboard().setText("http://127.0.0.1:13337/mcp")
        if self.conn_feedback_lbl:
            self.conn_feedback_lbl.setText(McpDashboardState.t("url_copied"))
            self.conn_feedback_lbl.setStyleSheet("background-color: #064E3B; color: #34D399; border: 1px solid #34D399; border-radius: 4px; padding: 4px 8px; font-size: 11px; font-weight: 600;")
            self.conn_feedback_lbl.setVisible(True)
            if QtCore:
                QtCore.QTimer.singleShot(3000, lambda: self.conn_feedback_lbl.setVisible(False))

    def _apply_test_connection_result(self, ok, msg):
        if self.status_pill:
            if ok:
                self.status_pill.setText(McpDashboardState.t("mcp_online"))
                self.status_pill.setStyleSheet("background-color: #064E3B; color: #34D399; border: 1px solid #34D399; border-radius: 12px; padding: 3px 12px; font-size: 11px; font-weight: 600;")
            else:
                self.status_pill.setText(McpDashboardState.t("mcp_offline"))
                self.status_pill.setStyleSheet("background-color: #4C0519; color: #FB7185; border: 1px solid #FB7185; border-radius: 12px; padding: 3px 12px; font-size: 11px; font-weight: 600;")

        if self.conn_feedback_lbl:
            if ok:
                self.conn_feedback_lbl.setText(McpDashboardState.t("conn_success", msg=msg))
                self.conn_feedback_lbl.setStyleSheet("background-color: #064E3B; color: #34D399; border: 1px solid #34D399; border-radius: 4px; padding: 4px 8px; font-size: 11px; font-weight: 600;")
            else:
                self.conn_feedback_lbl.setText(McpDashboardState.t("conn_failed", msg=msg))
                self.conn_feedback_lbl.setStyleSheet("background-color: #4C0519; color: #FB7185; border: 1px solid #FB7185; border-radius: 4px; padding: 4px 8px; font-size: 11px; font-weight: 600;")
            self.conn_feedback_lbl.setVisible(True)
            if QtCore:
                QtCore.QTimer.singleShot(4000, lambda: self.conn_feedback_lbl.setVisible(False))

        if self.test_conn_btn:
            self.test_conn_btn.setEnabled(True)
            self.test_conn_btn.setText(McpDashboardState.t("test_connection"))

        if "McpDashboardState" in globals():
            McpDashboardState.log_event("ui_action", f"MCP Connection Test: {msg}")

    def on_test_mcp_clicked(self):
        if self.test_conn_btn:
            self.test_conn_btn.setEnabled(False)
            self.test_conn_btn.setText(McpDashboardState.t("testing_connection"))
        if self.conn_feedback_lbl:
            self.conn_feedback_lbl.setText(McpDashboardState.t("testing_endpoint"))
            self.conn_feedback_lbl.setStyleSheet("background-color: #1E293B; color: #CBD5E1; border: 1px solid #334155; border-radius: 4px; padding: 4px 8px; font-size: 11px; font-weight: 600;")
            self.conn_feedback_lbl.setVisible(True)

        result_holder = []

        def _test_worker():
            ok = False
            msg = "Unknown error"
            try:
                if "McpDashboardState" in globals():
                    ok, msg = McpDashboardState.test_mcp_connection()
                else:
                    t0 = time.time()
                    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "get_metadata", "params": []}).encode("utf-8")
                    req = urllib.request.Request("http://127.0.0.1:13337/mcp", data=payload, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        dt = int((time.time() - t0) * 1000)
                        if "result" in data:
                            ok = True
                            mod = data["result"].get("module", "IDA") if isinstance(data["result"], dict) else "IDA"
                            msg = f"Connected in {dt} ms - Module: {mod}"
                        else:
                            msg = f"Error response: {data.get('error')}"
            except Exception as e:
                msg = f"Connection failed: {e}"
            result_holder.append((ok, msg))

        th = threading.Thread(target=_test_worker, daemon=True)
        th.start()

        if QtCore:
            poll_timer = QtCore.QTimer(self.test_conn_btn)
            t_start = time.time()
            def _on_poll():
                if result_holder:
                    poll_timer.stop()
                    poll_timer.deleteLater()
                    ok, msg = result_holder[0]
                    self._apply_test_connection_result(ok, msg)
                elif time.time() - t_start > 5.0:
                    poll_timer.stop()
                    poll_timer.deleteLater()
                    self._apply_test_connection_result(False, "Connection test timed out after 5s")
            poll_timer.timeout.connect(_on_poll)
            poll_timer.start(30)
        else:
            th.join(timeout=3)
            if result_holder:
                self._apply_test_connection_result(result_holder[0][0], result_holder[0][1])
            else:
                self._apply_test_connection_result(False, "Test completed without result")

    def refresh_all(self):
        try:
            info = None
            if "McpDashboardState" in globals():
                info = McpDashboardState.get_binary_info()
            if info and self.bin_lbl:
                self.bin_lbl.setText(f"{info['name']} ({info['proc']} 64-bit: {info['is_64']}) | {info['total_funcs']} {McpDashboardState.t('funcs_suffix')}")
            self.populate_functions_combo()
            self.refresh_cursor_info()
            self.update_telemetry_ui()
        except Exception:
            pass

    def update_telemetry_ui(self):
        try:
            total_reqs = 0
            if "McpDashboardState" in globals():
                total_reqs = McpDashboardState.total_rpc_requests
            if self.reqs_badge:
                self.reqs_badge.setText(McpDashboardState.t("reqs_badge", count=total_reqs))
            self.populate_tools_table()
        except Exception:
            pass

    def on_agent_combo_changed(self, idx):
        if idx < 0:
            return
        key = self.agent_combo.itemData(idx)
        if key:
            if "McpDashboardState" in globals():
                McpDashboardState.set_active_agent(key)
            self.on_agent_changed(key)

    def on_agent_changed(self, agent_key):
        t = lambda k, **kw: McpDashboardState.t(k, **kw)
        if self.agent_pill:
            self.agent_pill.setText(f"{t('agent_prefix')} {agent_key}")
        if self.btn_export:
            self.btn_export.setText(t("btn_send_to_agent", agent=agent_key))
        if self.prompt_input:
            self.prompt_input.setPlaceholderText(t("ask_placeholder", agent=agent_key))
        if self.ask_btn:
            self.ask_btn.setText(t("ask_btn", agent=agent_key))
        if hasattr(self, "log_agent_lbl") and self.log_agent_lbl:
            self.log_agent_lbl.setText(t("log_stream_title", agent=agent_key))
        if agent_key == "Direct_API":
            prov = self.provider_combo.currentText().strip() if self.provider_combo else "Direct AI"
            model = self.model_combo.currentText().strip() if self.model_combo else ""
            if self.copilot_agent_badge:
                self.copilot_agent_badge.setText(f"Provider: {prov}")
            if self.copilot_model_badge:
                self.copilot_model_badge.setText(f"Model: {model if model else 'none'}")
                self.copilot_model_badge.setVisible(True)
        else:
            if self.copilot_agent_badge:
                self.copilot_agent_badge.setText(f"{t('agent_prefix')} {agent_key}")
            if self.copilot_model_badge:
                self.copilot_model_badge.setVisible(False)
        self.update_agent_view()

    def create_custom_agent_panel(self):
        panel = QtWidgets.QFrame()
        panel.setObjectName("customAgentPanel")
        panel.setStyleSheet("background-color: #171D28; border: 1px solid #283142; border-radius: 8px;")
        l = QtWidgets.QVBoxLayout(panel)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(8)

        p_header = QtWidgets.QHBoxLayout()
        self.p_title = QtWidgets.QLabel(McpDashboardState.t("custom_agent_title"))
        self.p_title.setStyleSheet("font-size: 12px; font-weight: 700; color: #38BDF8;")
        p_header.addWidget(self.p_title)

        self.custom_agent_badge = QtWidgets.QLabel(McpDashboardState.t("direct_llm_mode"))
        self.custom_agent_badge.setStyleSheet("background-color: #1E293B; color: #CBD5E1; border: 1px solid #334155; border-radius: 4px; padding: 2px 8px; font-size: 10px; font-weight: 600;")
        p_header.addWidget(self.custom_agent_badge)
        p_header.addStretch()
        l.addLayout(p_header)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self.lbl_prov = QtWidgets.QLabel(McpDashboardState.t("provider_name_lbl"))
        self.lbl_prov.setStyleSheet("color: #E8EDF5; font-weight: 600; font-size: 11px;")
        grid.addWidget(self.lbl_prov, 0, 0)

        self.provider_combo = QtWidgets.QComboBox()
        self.provider_combo.setEditable(True)
        self.configure_bright_combo(self.provider_combo)
        self.provider_combo.addItem("KunciAI", "KunciAI")
        self.provider_combo.addItem("OpenAI", "OpenAI")
        self.provider_combo.addItem("DeepSeek", "DeepSeek")
        self.provider_combo.addItem("Groq", "Groq")
        self.provider_combo.addItem("OpenRouter", "OpenRouter")
        self.provider_combo.addItem("Gemini", "Gemini")
        self.provider_combo.addItem("Ollama (Local)", "Ollama")
        self.provider_combo.addItem("Custom", "Custom")
        self.provider_combo.currentIndexChanged.connect(self.on_provider_selected)
        grid.addWidget(self.provider_combo, 0, 1)

        self.lbl_url = QtWidgets.QLabel(McpDashboardState.t("base_url_lbl"))
        self.lbl_url.setStyleSheet("color: #E8EDF5; font-weight: 600; font-size: 11px;")
        grid.addWidget(self.lbl_url, 0, 2)

        self.base_url_input = QtWidgets.QLineEdit()
        self.base_url_input.setPlaceholderText("https://kunciai.id/v1 or https://api.openai.com/v1")
        self.base_url_input.textChanged.connect(self.on_custom_input_debounced)
        grid.addWidget(self.base_url_input, 0, 3)

        self.lbl_key = QtWidgets.QLabel(McpDashboardState.t("api_key_lbl"))
        self.lbl_key.setStyleSheet("color: #E8EDF5; font-weight: 600; font-size: 11px;")
        grid.addWidget(self.lbl_key, 1, 0)

        key_box = QtWidgets.QHBoxLayout()
        key_box.setSpacing(4)
        self.api_key_input = QtWidgets.QLineEdit()
        self.api_key_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key_input.setPlaceholderText(McpDashboardState.t("api_key_placeholder"))
        self.api_key_input.textChanged.connect(self.on_custom_input_debounced)
        key_box.addWidget(self.api_key_input)

        self.toggle_key_btn = QtWidgets.QPushButton(McpDashboardState.t("show_key"))
        self.toggle_key_btn.setMaximumWidth(70)
        self.toggle_key_btn.clicked.connect(self.on_toggle_api_key_visibility)
        key_box.addWidget(self.toggle_key_btn)
        grid.addLayout(key_box, 1, 1)

        self.lbl_model = QtWidgets.QLabel(McpDashboardState.t("model_id_lbl"))
        self.lbl_model.setStyleSheet("color: #E8EDF5; font-weight: 600; font-size: 11px;")
        grid.addWidget(self.lbl_model, 1, 2)

        model_box = QtWidgets.QHBoxLayout()
        model_box.setSpacing(6)
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setEditable(True)
        self.configure_bright_combo(self.model_combo)
        self.model_combo.setMinimumWidth(220)
        self.model_combo.currentIndexChanged.connect(self.on_model_combo_changed)
        self.model_combo.setToolTip(McpDashboardState.t("model_combo_tooltip"))
        model_box.addWidget(self.model_combo)

        self.models_count_lbl = QtWidgets.QLabel(McpDashboardState.t("models_count", count=0))
        self.models_count_lbl.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: 600;")
        model_box.addWidget(self.models_count_lbl)
        grid.addLayout(model_box, 1, 3)

        l.addLayout(grid)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)

        self.fetch_models_btn = QtWidgets.QPushButton(McpDashboardState.t("fetch_models"))
        self.fetch_models_btn.setObjectName("primaryAction")
        self.fetch_models_btn.clicked.connect(lambda: self.on_fetch_models_clicked(force=True, auto=False))
        btn_row.addWidget(self.fetch_models_btn)

        self.test_provider_conn_btn = QtWidgets.QPushButton(McpDashboardState.t("test_connection"))
        self.test_provider_conn_btn.clicked.connect(self.on_test_provider_connection_clicked)
        btn_row.addWidget(self.test_provider_conn_btn)

        self.test_model_btn = QtWidgets.QPushButton(McpDashboardState.t("test_selected_model"))
        self.test_model_btn.setObjectName("accentAction")
        self.test_model_btn.clicked.connect(self.on_test_selected_model_clicked)
        btn_row.addWidget(self.test_model_btn)

        self.save_custom_agent_btn = QtWidgets.QPushButton(McpDashboardState.t("save_agent_config"))
        self.save_custom_agent_btn.setObjectName("successAction")
        self.save_custom_agent_btn.clicked.connect(self.on_save_custom_agent_clicked)
        btn_row.addWidget(self.save_custom_agent_btn)

        btn_row.addStretch()
        l.addLayout(btn_row)

        stat_row = QtWidgets.QHBoxLayout()
        self.fetch_status_lbl = QtWidgets.QLabel(McpDashboardState.t("fetch_status_initial"))
        self.fetch_status_lbl.setStyleSheet("color: #CBD5E1; font-size: 11px; font-weight: 500;")
        stat_row.addWidget(self.fetch_status_lbl)

        self.model_meta_lbl = QtWidgets.QLabel("")
        self.model_meta_lbl.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: 600;")
        stat_row.addWidget(self.model_meta_lbl)
        stat_row.addStretch()
        l.addLayout(stat_row)

        self.custom_agent_status_lbl = QtWidgets.QLabel("")
        self.custom_agent_status_lbl.setStyleSheet("font-size: 11px; font-weight: 600; padding: 4px 8px; border-radius: 4px;")
        self.custom_agent_status_lbl.setVisible(False)
        l.addWidget(self.custom_agent_status_lbl)

        return panel

    def load_custom_agent_form_values(self):
        state_cls = globals().get("McpDashboardState") or getattr(sys.modules.get(__name__), "McpDashboardState", None)
        if not state_cls:
            return
        direct_cfg = state_cls.agent_config.get("agents", {}).get("Direct_API", {})
        prov = direct_cfg.get("provider", "KunciAI")
        url = direct_cfg.get("base_url", "https://kunciai.id/v1")
        model = direct_cfg.get("model", "")
        enc_key = direct_cfg.get("api_key_enc", "")
        raw_key = direct_cfg.get("api_key", "")
        if enc_key and hasattr(state_cls, "decode_secret"):
            key = state_cls.decode_secret(enc_key)
        else:
            key = raw_key

        if self.provider_combo:
            idx = self.provider_combo.findText(prov)
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)
            else:
                self.provider_combo.setEditText(prov)

        if self.base_url_input:
            self.base_url_input.blockSignals(True)
            self.base_url_input.setText(url)
            self.base_url_input.blockSignals(False)

        if self.api_key_input:
            self.api_key_input.blockSignals(True)
            self.api_key_input.setText(key)
            self.api_key_input.blockSignals(False)

        if self.model_combo:
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            if model:
                self.model_combo.addItem(model, {"id": model})
                self.model_combo.setCurrentIndex(0)
            self.model_combo.blockSignals(False)

        cache_key = f"{prov}:{url.strip().rstrip('/')}"
        if hasattr(state_cls, "_models_cache") and cache_key in state_cls._models_cache:
            cached = state_cls._models_cache[cache_key]
            msg = "Memuat dari cache lokal" if McpDashboardState.get_language() == "id" else "Loaded from local cache"
            self._apply_fetched_models(True, cached.get("models", []), msg)

    def on_provider_selected(self, idx):
        if not self.provider_combo or not self.base_url_input:
            return
        prov = self.provider_combo.currentText().strip()
        presets = {
            "KunciAI": "https://kunciai.id/v1",
            "OpenAI": "https://api.openai.com/v1",
            "DeepSeek": "https://api.deepseek.com/v1",
            "Groq": "https://api.groq.com/openai/v1",
            "OpenRouter": "https://openrouter.ai/api/v1",
            "Gemini": "https://generativelanguage.googleapis.com/v1beta",
            "Ollama (Local)": "http://localhost:11434/v1"
        }
        if prov in presets:
            self.base_url_input.setText(presets[prov])
            if self.model_combo:
                self.model_combo.clear()
            if self.models_count_lbl:
                self.models_count_lbl.setText(McpDashboardState.t("models_count", count=0))
            if self.fetch_status_lbl:
                msg = "Penyedia diganti. Klik 'Ambil Model' atau tunggu pengambilan otomatis." if McpDashboardState.get_language() == "id" else "Provider changed. Click 'Fetch Models' or wait for auto-fetch."
                self.fetch_status_lbl.setText(msg)
                self.fetch_status_lbl.setStyleSheet("color: #CBD5E1; font-size: 11px; font-weight: 500;")

    def on_toggle_api_key_visibility(self):
        if not self.api_key_input or not self.toggle_key_btn:
            return
        self._active_key_visible = not self._active_key_visible
        if self._active_key_visible:
            self.api_key_input.setEchoMode(QtWidgets.QLineEdit.Normal)
            self.toggle_key_btn.setText(McpDashboardState.t("hide_key"))
        else:
            self.api_key_input.setEchoMode(QtWidgets.QLineEdit.Password)
            self.toggle_key_btn.setText(McpDashboardState.t("show_key"))

    def on_custom_input_debounced(self):
        if not QtCore:
            return
        if self.auto_fetch_timer:
            self.auto_fetch_timer.stop()
            self.auto_fetch_timer.deleteLater()
            self.auto_fetch_timer = None
        self.auto_fetch_timer = QtCore.QTimer(self.custom_agent_panel)
        self.auto_fetch_timer.setSingleShot(True)
        self.auto_fetch_timer.timeout.connect(self._on_debounce_timer_fired)
        self.auto_fetch_timer.start(850)

    def _on_debounce_timer_fired(self):
        if not self.base_url_input:
            return
        url = self.base_url_input.text().strip()
        key = self.api_key_input.text().strip() if self.api_key_input else ""
        prov = self.provider_combo.currentText().strip() if self.provider_combo else ""
        if url.startswith("http://") or url.startswith("https://"):
            if len(key) >= 4 or prov.lower() == "ollama":
                self.on_fetch_models_clicked(force=False, auto=True)

    def on_fetch_models_clicked(self, force=True, auto=False):
        if not self.base_url_input:
            return
        url = self.base_url_input.text().strip()
        key = self.api_key_input.text().strip() if self.api_key_input else ""
        prov = self.provider_combo.currentText().strip() if self.provider_combo else "OpenAI"
        lang = McpDashboardState.get_language()

        if not url:
            if not auto:
                msg = "URL Basis tidak boleh kosong" if lang == "id" else "Base URL cannot be empty"
                self.set_custom_agent_status(msg, False)
            return

        if not key and prov.lower() != "ollama":
            if not auto:
                msg = "Kunci API wajib diisi untuk mengambil model" if lang == "id" else "API Key is required to fetch models"
                self.set_custom_agent_status(msg, False)
            return

        self._fetch_seq += 1
        seq = self._fetch_seq

        if self.fetch_models_btn:
            self.fetch_models_btn.setEnabled(False)
            self.fetch_models_btn.setText(McpDashboardState.t("fetching_models"))

        if self.fetch_status_lbl:
            self.fetch_status_lbl.setText(McpDashboardState.t("fetch_status_fetching"))
            self.fetch_status_lbl.setStyleSheet("color: #FBBF24; font-size: 11px;")

        res_holder = []
        def _fetch_worker():
            state_cls = globals().get("McpDashboardState") or getattr(sys.modules.get(__name__), "McpDashboardState", None)
            if state_cls and hasattr(state_cls, "fetch_provider_models"):
                ok, models, msg = state_cls.fetch_provider_models(prov, url, key, force_refresh=force)
                res_holder.append((ok, models, msg))
            else:
                res_holder.append((False, [], "Backend state unavailable"))

        th = threading.Thread(target=_fetch_worker, daemon=True)
        th.start()

        if QtCore:
            poll_timer = QtCore.QTimer(self.custom_agent_panel)
            t_start = time.time()
            def _poll():
                if seq != self._fetch_seq:
                    poll_timer.stop()
                    poll_timer.deleteLater()
                    return
                if res_holder:
                    poll_timer.stop()
                    poll_timer.deleteLater()
                    ok, models, msg = res_holder[0]
                    self._apply_fetched_models(ok, models, msg)
                elif time.time() - t_start > 12.0:
                    poll_timer.stop()
                    poll_timer.deleteLater()
                    timeout_msg = "Permintaan ambil model timeout setelah 12s" if lang == "id" else "Model fetch request timed out after 12s"
                    self._apply_fetched_models(False, [], timeout_msg)
            poll_timer.timeout.connect(_poll)
            poll_timer.start(40)
        else:
            th.join(timeout=3)
            if res_holder:
                self._apply_fetched_models(res_holder[0][0], res_holder[0][1], res_holder[0][2])
            else:
                empty_resp = "Pengambilan selesai tanpa respons" if lang == "id" else "Fetch completed without response"
                self._apply_fetched_models(False, [], empty_resp)

    def _apply_fetched_models(self, ok, models, msg):
        if self.fetch_models_btn:
            self.fetch_models_btn.setEnabled(True)
            self.fetch_models_btn.setText(McpDashboardState.t("refresh_models"))
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        lang = McpDashboardState.get_language()

        if ok and models:
            prev_selected = self.model_combo.currentText().strip() if self.model_combo else ""
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            for m in models:
                mid = m["id"]
                self.model_combo.addItem(mid, m)
                if QtCore and QtGui:
                    cnt = self.model_combo.count()
                    self.model_combo.setItemData(cnt - 1, QtGui.QColor("#F8FAFC"), QtCore.Qt.ForegroundRole)

            found_prev = False
            if prev_selected:
                idx = self.model_combo.findText(prev_selected)
                if idx >= 0:
                    self.model_combo.setCurrentIndex(idx)
                    found_prev = True

            if not found_prev and prev_selected:
                self.model_combo.setEditText(prev_selected)
                warn_msg = f"Peringatan: Model tersimpan '{prev_selected}' tidak ditemukan di penyedia. Silakan pilih dari dropdown." if lang == "id" else f"Warning: Saved model '{prev_selected}' not found on provider. Select from dropdown."
                self.set_custom_agent_status(warn_msg, False)
            elif models and not prev_selected:
                self.model_combo.setCurrentIndex(0)

            self.model_combo.blockSignals(False)

            if self.models_count_lbl:
                self.models_count_lbl.setText(McpDashboardState.t("models_count", count=len(models)))
            if self.fetch_status_lbl:
                self.fetch_status_lbl.setText(McpDashboardState.t("fetch_status_success", count=len(models), time=now_str))
                self.fetch_status_lbl.setStyleSheet("color: #34D399; font-size: 11px; font-weight: 600;")
            self.update_model_metadata_view()

        elif ok and not models:
            if self.model_combo:
                self.model_combo.clear()
            if self.models_count_lbl:
                self.models_count_lbl.setText(McpDashboardState.t("models_count", count=0))
            if self.fetch_status_lbl:
                self.fetch_status_lbl.setText(McpDashboardState.t("fetch_status_empty", time=now_str))
                self.fetch_status_lbl.setStyleSheet("color: #FBBF24; font-size: 11px;")
        else:
            if self.fetch_status_lbl:
                self.fetch_status_lbl.setText(McpDashboardState.t("fetch_status_failed", time=now_str, msg=msg))
                self.fetch_status_lbl.setStyleSheet("color: #FB7185; font-size: 11px; font-weight: 600;")
            self.set_custom_agent_status(msg, False)

    def update_model_metadata_view(self):
        if not self.model_combo or not self.model_meta_lbl:
            return
        idx = self.model_combo.currentIndex()
        meta = self.model_combo.itemData(idx)
        if isinstance(meta, dict) and "meta" in meta and meta["meta"]:
            m = meta["meta"]
            parts = []
            if "displayName" in m:
                parts.append(f"Name: {m['displayName']}")
            if "owned_by" in m:
                parts.append(f"Owner: {m['owned_by']}")
            if "context_window" in m:
                parts.append(f"Context: {m['context_window']}")
            self.model_meta_lbl.setText(" | ".join(parts))
        else:
            self.model_meta_lbl.setText("")

    def on_model_combo_changed(self, idx):
        if not self.model_combo:
            return
        m_id = self.model_combo.currentText().strip()
        state_cls = globals().get("McpDashboardState") or getattr(sys.modules.get(__name__), "McpDashboardState", None)
        if state_cls and hasattr(state_cls, "agent_config"):
            direct_cfg = state_cls.agent_config.setdefault("agents", {}).setdefault("Direct_API", {})
            direct_cfg["model"] = m_id
        if self.copilot_model_badge and self.copilot_model_badge.isVisible():
            self.copilot_model_badge.setText(f"Model: {m_id if m_id else 'none'}")
        self.update_model_metadata_view()

    def on_test_provider_connection_clicked(self):
        if not self.base_url_input:
            return
        prov = self.provider_combo.currentText().strip() if self.provider_combo else "OpenAI"
        url = self.base_url_input.text().strip()
        key = self.api_key_input.text().strip() if self.api_key_input else ""
        lang = McpDashboardState.get_language()

        if not url:
            msg = "URL Basis tidak boleh kosong" if lang == "id" else "Base URL cannot be empty"
            self.set_custom_agent_status(msg, False)
            return

        if self.test_provider_conn_btn:
            self.test_provider_conn_btn.setEnabled(False)
            self.test_provider_conn_btn.setText(McpDashboardState.t("testing_connection"))
        test_msg = f"Menguji koneksi ke {url}..." if lang == "id" else f"Testing connection to {url}..."
        self.set_custom_agent_status(test_msg, True)

        res_holder = []
        def _worker():
            state_cls = globals().get("McpDashboardState") or getattr(sys.modules.get(__name__), "McpDashboardState", None)
            if state_cls and hasattr(state_cls, "test_provider_connection"):
                ok, msg = state_cls.test_provider_connection(prov, url, key)
                res_holder.append((ok, msg))
            else:
                res_holder.append((False, "Backend state unavailable"))

        th = threading.Thread(target=_worker, daemon=True)
        th.start()

        if QtCore:
            poll_timer = QtCore.QTimer(self.custom_agent_panel)
            t_start = time.time()
            def _poll():
                if res_holder:
                    poll_timer.stop()
                    poll_timer.deleteLater()
                    ok, msg = res_holder[0]
                    if self.test_provider_conn_btn:
                        self.test_provider_conn_btn.setEnabled(True)
                        self.test_provider_conn_btn.setText(McpDashboardState.t("test_connection"))
                    self.set_custom_agent_status(msg, ok)
                elif time.time() - t_start > 12.0:
                    poll_timer.stop()
                    poll_timer.deleteLater()
                    if self.test_provider_conn_btn:
                        self.test_provider_conn_btn.setEnabled(True)
                        self.test_provider_conn_btn.setText(McpDashboardState.t("test_connection"))
                    t_out = "Uji koneksi timeout setelah 12s" if lang == "id" else "Connection test timed out after 12s"
                    self.set_custom_agent_status(t_out, False)
            poll_timer.timeout.connect(_poll)
            poll_timer.start(40)
        else:
            th.join(timeout=3)
            if self.test_provider_conn_btn:
                self.test_provider_conn_btn.setEnabled(True)
                self.test_provider_conn_btn.setText(McpDashboardState.t("test_connection"))
            if res_holder:
                self.set_custom_agent_status(res_holder[0][1], res_holder[0][0])
            else:
                done_msg = "Uji selesai tanpa hasil" if lang == "id" else "Test finished without result"
                self.set_custom_agent_status(done_msg, False)

    def on_test_selected_model_clicked(self):
        if not self.base_url_input or not self.model_combo:
            return
        prov = self.provider_combo.currentText().strip() if self.provider_combo else "OpenAI"
        url = self.base_url_input.text().strip()
        key = self.api_key_input.text().strip() if self.api_key_input else ""
        model = self.model_combo.currentText().strip()
        lang = McpDashboardState.get_language()

        if not model:
            msg = "Pilih atau ketik ID Model terlebih dahulu untuk diuji" if lang == "id" else "Select or type a Model ID first to test"
            self.set_custom_agent_status(msg, False)
            return

        if self.test_model_btn:
            self.test_model_btn.setEnabled(False)
            self.test_model_btn.setText(McpDashboardState.t("testing_model"))
        init_msg = f"Mengirim inferensi uji untuk model '{model}'..." if lang == "id" else f"Sending test inference for model '{model}'..."
        self.set_custom_agent_status(init_msg, True)

        res_holder = []
        def _worker():
            state_cls = globals().get("McpDashboardState") or getattr(sys.modules.get(__name__), "McpDashboardState", None)
            if state_cls and hasattr(state_cls, "test_selected_model_inference"):
                ok, msg = state_cls.test_selected_model_inference(prov, url, key, model)
                res_holder.append((ok, msg))
            else:
                res_holder.append((False, "Backend state unavailable"))

        th = threading.Thread(target=_worker, daemon=True)
        th.start()

        if QtCore:
            poll_timer = QtCore.QTimer(self.custom_agent_panel)
            t_start = time.time()
            def _poll():
                if res_holder:
                    poll_timer.stop()
                    poll_timer.deleteLater()
                    ok, msg = res_holder[0]
                    if self.test_model_btn:
                        self.test_model_btn.setEnabled(True)
                        self.test_model_btn.setText(McpDashboardState.t("test_selected_model"))
                    self.set_custom_agent_status(msg, ok)
                elif time.time() - t_start > 15.0:
                    poll_timer.stop()
                    poll_timer.deleteLater()
                    if self.test_model_btn:
                        self.test_model_btn.setEnabled(True)
                        self.test_model_btn.setText(McpDashboardState.t("test_selected_model"))
                    t_out = "Uji inferensi model timeout setelah 15s" if lang == "id" else "Model inference test timed out after 15s"
                    self.set_custom_agent_status(t_out, False)
            poll_timer.timeout.connect(_poll)
            poll_timer.start(40)
        else:
            th.join(timeout=3)
            if self.test_model_btn:
                self.test_model_btn.setEnabled(True)
                self.test_model_btn.setText(McpDashboardState.t("test_selected_model"))
            if res_holder:
                self.set_custom_agent_status(res_holder[0][1], res_holder[0][0])
            else:
                done_msg = "Uji inferensi selesai tanpa hasil" if lang == "id" else "Inference test completed without result"
                self.set_custom_agent_status(done_msg, False)

    def on_save_custom_agent_clicked(self):
        if not self.base_url_input or not self.model_combo:
            return
        prov = self.provider_combo.currentText().strip() if self.provider_combo else "Custom"
        url = self.base_url_input.text().strip()
        key = self.api_key_input.text().strip() if self.api_key_input else ""
        model = self.model_combo.currentText().strip()
        lang = McpDashboardState.get_language()

        if not url:
            msg = "URL Basis tidak boleh kosong" if lang == "id" else "Base URL cannot be empty"
            self.set_custom_agent_status(msg, False)
            return

        state_cls = globals().get("McpDashboardState") or getattr(sys.modules.get(__name__), "McpDashboardState", None)
        if state_cls and hasattr(state_cls, "save_custom_agent_config"):
            state_cls.save_custom_agent_config(prov, url, key, model)
            msg = f"Konfigurasi {prov} berhasil disimpan! Model: {model}" if lang == "id" else f"Configuration for {prov} saved! Model: {model}"
            self.set_custom_agent_status(msg, True)
        else:
            msg = "Konfigurasi tersimpan lokal" if lang == "id" else "Configuration saved locally"
            self.set_custom_agent_status(msg, True)

        if self.copilot_agent_badge:
            self.copilot_agent_badge.setText(f"Provider: {prov}")
        if self.copilot_model_badge:
            self.copilot_model_badge.setText(f"Model: {model if model else 'none'}")
            self.copilot_model_badge.setVisible(True)

    def set_custom_agent_status(self, msg, ok=True):
        if not self.custom_agent_status_lbl:
            return
        color = "#34D399" if ok else "#FB7185"
        bg = "#064E3B" if ok else "#4C0519"
        border = "#059669" if ok else "#9F1239"
        self.custom_agent_status_lbl.setText(msg)
        self.custom_agent_status_lbl.setStyleSheet(f"background-color: {bg}; color: {color}; border: 1px solid {border}; border-radius: 4px; padding: 4px 8px; font-size: 11px; font-weight: 600;")
        self.custom_agent_status_lbl.setVisible(True)
        if QtCore:
            QtCore.QTimer.singleShot(6000, lambda: self.custom_agent_status_lbl.setVisible(False) if self.custom_agent_status_lbl else None)

    def update_agent_view(self):
        agent_key = "Antigravity"
        info = {}
        if "McpDashboardState" in globals():
            agent_key = McpDashboardState.get_active_agent_key()
            info = McpDashboardState.get_active_agent_info()

        if agent_key == "Direct_API":
            if self.custom_agent_panel:
                self.custom_agent_panel.setVisible(True)
            if self.agent_desc_box:
                self.agent_desc_box.setVisible(False)
            if self.copy_agent_cfg_btn:
                self.copy_agent_cfg_btn.setVisible(False)
            if self.save_agent_cfg_btn:
                self.save_agent_cfg_btn.setVisible(False)
            if self.run_local_agent_btn:
                self.run_local_agent_btn.setVisible(False)
            self.load_custom_agent_form_values()
            return

        if self.custom_agent_panel:
            self.custom_agent_panel.setVisible(False)
        if self.agent_desc_box:
            self.agent_desc_box.setVisible(True)
        if not self.agent_desc_box:
            return

        lang = McpDashboardState.get_language()
        if lang == "id":
            if agent_key == "Antigravity":
                text = (
                    "<div style='font-size: 13px; font-weight: 700; color: #8B5CF6;'>Agen Antigravity (Google DeepMind)</div>"
                    "<div style='margin-top: 4px; color: #E8EDF5; line-height: 140%;'>"
                    "• Konfigurasi Klien MCP: <span style='font-family: monospace; color: #38BDF8;'>c:\\Users\\user\\Documents\\project\\ida-mcp.json</span><br/>"
                    "• Antigravity terhubung langsung ke IDA Pro melalui endpoint: <span style='font-family: monospace; color: #34D399;'>http://127.0.0.1:13337/mcp</span><br/>"
                    "• Klik <b>'Salin Konfigurasi Agen'</b> untuk menyalin konfigurasi JSON server Antigravity.<br/>"
                    "• Konteks analisis disinkronkan otomatis ke <span style='font-family: monospace; color: #38BDF8;'>ida_agent_context.json</span> & <span style='font-family: monospace; color: #38BDF8;'>ida_current_function.c</span>."
                    "</div>"
                )
                self.copy_agent_cfg_btn.setVisible(True)
                self.run_local_agent_btn.setVisible(False)
                self.save_agent_cfg_btn.setVisible(True)
            elif agent_key == "Claude":
                text = (
                    "<div style='font-size: 13px; font-weight: 700; color: #8B5CF6;'>Claude Desktop / Claude Code (Anthropic)</div>"
                    "<div style='margin-top: 4px; color: #E8EDF5; line-height: 140%;'>"
                    "• Tambahkan blok konfigurasi ini ke <span style='font-family: monospace; color: #38BDF8;'>%APPDATA%\\Claude\\claude_desktop_config.json</span>:<br/>"
                    "<pre style='background: #11151D; border: 1px solid #283142; border-radius: 4px; padding: 6px; color: #34D399; font-size: 11px;'>"
                    '{\\n  "mcpServers": {\\n    "ida-pro": {\\n      "command": "C:\\\\Python314\\\\python.exe",\\n'
                    '      "args": ["C:\\\\Users\\\\user\\\\AppData\\\\Roaming\\\\Python\\\\Python314\\\\site-packages\\\\ida_pro_mcp\\\\server.py", "--ida-rpc", "http://127.0.0.1:13337"]\\n'
                    '    }\\n  }\\n}'
                    "</pre>"
                    "</div>"
                )
                self.copy_agent_cfg_btn.setVisible(True)
                self.run_local_agent_btn.setVisible(False)
                self.save_agent_cfg_btn.setVisible(True)
            elif agent_key == "Cursor":
                text = (
                    "<div style='font-size: 13px; font-weight: 700; color: #8B5CF6;'>Cursor IDE / Windsurf (Editor Kode AI)</div>"
                    "<div style='margin-top: 4px; color: #E8EDF5; line-height: 140%;'>"
                    "• Tambahkan konfigurasi ke <span style='font-family: monospace; color: #38BDF8;'>.cursor/mcp.json</span> di direktori utama ruang kerja Anda:<br/>"
                    "<pre style='background: #11151D; border: 1px solid #283142; border-radius: 4px; padding: 6px; color: #34D399; font-size: 11px;'>"
                    '{\\n  "mcpServers": {\\n    "ida-pro": {\\n      "command": "python",\\n'
                    '      "args": ["-m", "ida_pro_mcp.server", "--ida-rpc", "http://127.0.0.1:13337"]\\n'
                    '    }\\n  }\\n}'
                    "</pre>"
                    "</div>"
                )
                self.copy_agent_cfg_btn.setVisible(True)
                self.run_local_agent_btn.setVisible(False)
                self.save_agent_cfg_btn.setVisible(True)
            elif agent_key == "Local_Script":
                text = (
                    "<div style='font-size: 13px; font-weight: 700; color: #8B5CF6;'>Skrip Otonom (ida_mcp_agent)</div>"
                    "<div style='margin-top: 4px; color: #E8EDF5; line-height: 140%;'>"
                    "• Eksekutor Skrip: <span style='font-family: monospace; color: #38BDF8;'>c:\\Users\\user\\Documents\\project\\ida_mcp_agent\\agent_runner.py</span><br/>"
                    "• Menyelesaikan pointer, memetakan referensi silang, dan menambahkan anotasi ke database IDA Pro secara otomatis.<br/>"
                    "• Klik <b>'Jalankan ida_mcp_agent'</b> untuk memicu analisis otomatis pada fungsi aktif."
                    "</div>"
                )
                self.copy_agent_cfg_btn.setVisible(False)
                self.run_local_agent_btn.setVisible(True)
                self.save_agent_cfg_btn.setVisible(True)
            else:
                name = info.get('name', 'Agen Kustom')
                desc = info.get('desc', '')
                text = f"<div style='font-size: 13px; font-weight: 700; color: #8B5CF6;'>{name}</div><div style='margin-top: 4px;'>{desc}</div>"
                self.copy_agent_cfg_btn.setVisible(False)
                self.run_local_agent_btn.setVisible(False)
                self.save_agent_cfg_btn.setVisible(True)
        else:
            if agent_key == "Antigravity":
                text = (
                    "<div style='font-size: 13px; font-weight: 700; color: #8B5CF6;'>Antigravity Agent (Google DeepMind)</div>"
                    "<div style='margin-top: 4px; color: #E8EDF5; line-height: 140%;'>"
                    "• MCP Client Configuration: <span style='font-family: monospace; color: #38BDF8;'>c:\\Users\\user\\Documents\\project\\ida-mcp.json</span><br/>"
                    "• Antigravity connects directly to IDA Pro via endpoint: <span style='font-family: monospace; color: #34D399;'>http://127.0.0.1:13337/mcp</span><br/>"
                    "• Click <b>'Copy Agent Config'</b> to copy the Antigravity server JSON configuration.<br/>"
                    "• Analysis context is automatically synced to <span style='font-family: monospace; color: #38BDF8;'>ida_agent_context.json</span> & <span style='font-family: monospace; color: #38BDF8;'>ida_current_function.c</span>."
                    "</div>"
                )
                self.copy_agent_cfg_btn.setVisible(True)
                self.run_local_agent_btn.setVisible(False)
                self.save_agent_cfg_btn.setVisible(True)
            elif agent_key == "Claude":
                text = (
                    "<div style='font-size: 13px; font-weight: 700; color: #8B5CF6;'>Claude Desktop / Claude Code (Anthropic)</div>"
                    "<div style='margin-top: 4px; color: #E8EDF5; line-height: 140%;'>"
                    "• Add this configuration block to <span style='font-family: monospace; color: #38BDF8;'>%APPDATA%\\Claude\\claude_desktop_config.json</span>:<br/>"
                    "<pre style='background: #11151D; border: 1px solid #283142; border-radius: 4px; padding: 6px; color: #34D399; font-size: 11px;'>"
                    '{\\n  "mcpServers": {\\n    "ida-pro": {\\n      "command": "C:\\\\Python314\\\\python.exe",\\n'
                    '      "args": ["C:\\\\Users\\\\user\\\\AppData\\\\Roaming\\\\Python\\\\Python314\\\\site-packages\\\\ida_pro_mcp\\\\server.py", "--ida-rpc", "http://127.0.0.1:13337"]\\n'
                    '    }\\n  }\\n}'
                    "</pre>"
                    "</div>"
                )
                self.copy_agent_cfg_btn.setVisible(True)
                self.run_local_agent_btn.setVisible(False)
                self.save_agent_cfg_btn.setVisible(True)
            elif agent_key == "Cursor":
                text = (
                    "<div style='font-size: 13px; font-weight: 700; color: #8B5CF6;'>Cursor IDE / Windsurf (AI Code Editor)</div>"
                    "<div style='margin-top: 4px; color: #E8EDF5; line-height: 140%;'>"
                    "• Add configuration to <span style='font-family: monospace; color: #38BDF8;'>.cursor/mcp.json</span> in your workspace root:<br/>"
                    "<pre style='background: #11151D; border: 1px solid #283142; border-radius: 4px; padding: 6px; color: #34D399; font-size: 11px;'>"
                    '{\\n  "mcpServers": {\\n    "ida-pro": {\\n      "command": "python",\\n'
                    '      "args": ["-m", "ida_pro_mcp.server", "--ida-rpc", "http://127.0.0.1:13337"]\\n'
                    '    }\\n  }\\n}'
                    "</pre>"
                    "</div>"
                )
                self.copy_agent_cfg_btn.setVisible(True)
                self.run_local_agent_btn.setVisible(False)
                self.save_agent_cfg_btn.setVisible(True)
            elif agent_key == "Local_Script":
                text = (
                    "<div style='font-size: 13px; font-weight: 700; color: #8B5CF6;'>Autonomous Script (ida_mcp_agent)</div>"
                    "<div style='margin-top: 4px; color: #E8EDF5; line-height: 140%;'>"
                    "• Script Runner: <span style='font-family: monospace; color: #38BDF8;'>c:\\Users\\user\\Documents\\project\\ida_mcp_agent\\agent_runner.py</span><br/>"
                    "• Resolves pointers, maps cross-references, and adds annotations into the IDA Pro database automatically.<br/>"
                    "• Click <b>'Run ida_mcp_agent'</b> to trigger autonomous analysis on the active function."
                    "</div>"
                )
                self.copy_agent_cfg_btn.setVisible(False)
                self.run_local_agent_btn.setVisible(True)
                self.save_agent_cfg_btn.setVisible(True)
            else:
                name = info.get('name', 'Custom Agent')
                desc = info.get('desc', '')
                text = f"<div style='font-size: 13px; font-weight: 700; color: #8B5CF6;'>{name}</div><div style='margin-top: 4px;'>{desc}</div>"
                self.copy_agent_cfg_btn.setVisible(False)
                self.run_local_agent_btn.setVisible(False)
                self.save_agent_cfg_btn.setVisible(True)

        self.agent_desc_box.setHtml(text)

    def on_copy_agent_config(self):
        agent_key = "Antigravity"
        if "McpDashboardState" in globals():
            agent_key = McpDashboardState.get_active_agent_key()
        cfg_snippet = {
            "mcpServers": {
                "ida-pro": {
                    "command": "C:\\Python314\\python.exe",
                    "args": [
                        "C:\\Users\\user\\AppData\\Roaming\\Python\\Python314\\site-packages\\ida_pro_mcp\\server.py",
                        "--ida-rpc",
                        "http://127.0.0.1:13337"
                    ]
                }
            }
        }
        if QtWidgets:
            QtWidgets.QApplication.clipboard().setText(json.dumps(cfg_snippet, indent=2))
        if self.agent_feedback_lbl:
            self.agent_feedback_lbl.setText(McpDashboardState.t("agent_cfg_copied"))
            if QtCore:
                QtCore.QTimer.singleShot(3000, lambda: self.agent_feedback_lbl.setText(""))
        if "McpDashboardState" in globals():
            McpDashboardState.log_event("ui_action", f"Copied MCP Config JSON for {agent_key} to clipboard")

    def on_save_agent_preference(self):
        if "McpDashboardState" in globals():
            McpDashboardState.save_agent_config()
            McpDashboardState.log_event("ui_action", "Saved agent preferences to ida_agent_config.json")
        if self.agent_feedback_lbl:
            self.agent_feedback_lbl.setText(McpDashboardState.t("agent_pref_saved"))
            if QtCore:
                QtCore.QTimer.singleShot(3000, lambda: self.agent_feedback_lbl.setText(""))

    def on_run_local_agent(self):
        func_info = None
        if "McpDashboardState" in globals():
            func_info = McpDashboardState.get_current_function_details()
        target_addr = func_info["start"] if func_info and "start" in func_info else "0x1d88ec"
        script_path = r"c:\Users\user\Documents\project\ida_mcp_agent\agent_runner.py"

        if "McpDashboardState" in globals():
            McpDashboardState.log_event("agent_call", f"Launching ida_mcp_agent for {target_addr}...")

        def _worker():
            try:
                import subprocess
                p = subprocess.Popen(
                    [sys.executable, script_path, target_addr],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=r"c:\Users\user\Documents\project\ida_mcp_agent"
                )
                for line in p.stdout:
                    l = line.strip()
                    if l and "McpDashboardState" in globals():
                        McpDashboardState.log_event("agent_success", l)
                p.wait()
                if "McpDashboardState" in globals():
                    McpDashboardState.log_event("agent_success", f"ida_mcp_agent completed for {target_addr}")
            except Exception as e:
                if "McpDashboardState" in globals():
                    McpDashboardState.log_event("agent_error", f"ida_mcp_agent error: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def get_category_color(self, category):
        cat = category.lower()
        if "nav" in cat:
            return QtGui.QColor("#38BDF8")
        if "call" in cat:
            return QtGui.QColor("#A78BFA")
        if "mod" in cat:
            return QtGui.QColor("#FBBF24")
        if "mem" in cat:
            return QtGui.QColor("#34D399")
        if "dbg" in cat or "debug" in cat:
            return QtGui.QColor("#FB7185")
        if "stack" in cat or "struct" in cat:
            return QtGui.QColor("#C084FC")
        return QtGui.QColor("#CBD5E1")

    def populate_tools_table(self):
        if not self.tools_table:
            return
        
        catalog = globals().get("MCP_TOOLS_CATALOG", [])
        query = self.tool_search.text().lower().strip() if hasattr(self, "tool_search") and self.tool_search else ""
        selected_cat = self.category_combo.currentData() if hasattr(self, "category_combo") and self.category_combo else ""

        filtered = []
        for t in catalog:
            if selected_cat and t.get("category", "").lower() != selected_cat.lower():
                continue
            if query:
                in_name = query in t.get("name", "").lower()
                in_cat = query in t.get("category", "").lower()
                in_desc = query in t.get("desc", "").lower()
                if not (in_name or in_cat or in_desc):
                    continue
            filtered.append(t)

        if self.tools_count_lbl:
            self.tools_count_lbl.setText(McpDashboardState.t("tools_showing_count", shown=len(filtered), total=len(catalog)))

        self.tools_table.setRowCount(len(filtered))
        stats = {}
        if "McpDashboardState" in globals():
            stats = McpDashboardState.rpc_stats

        font_mono = QtGui.QFont("Cascadia Code", 10)
        font_mono.setStyleHint(QtGui.QFont.Monospace)

        for row, t in enumerate(filtered):
            idx_item = QtWidgets.QTableWidgetItem(str(row + 1))
            idx_item.setTextAlignment(QtCore.Qt.AlignCenter)
            idx_item.setForeground(QtGui.QColor("#CBD5E1"))
            self.tools_table.setItem(row, 0, idx_item)

            name_item = QtWidgets.QTableWidgetItem(t["name"])
            name_item.setFont(font_mono)
            name_item.setForeground(QtGui.QColor("#38BDF8"))
            self.tools_table.setItem(row, 1, name_item)

            cat_item = QtWidgets.QTableWidgetItem(t["category"])
            cat_item.setTextAlignment(QtCore.Qt.AlignCenter)
            cat_item.setForeground(self.get_category_color(t["category"]))
            self.tools_table.setItem(row, 2, cat_item)

            calls_count = stats.get(t["name"], 0)
            calls_item = QtWidgets.QTableWidgetItem(str(calls_count))
            calls_item.setTextAlignment(QtCore.Qt.AlignCenter)
            if calls_count > 0:
                calls_item.setForeground(QtGui.QColor("#34D399"))
            else:
                calls_item.setForeground(QtGui.QColor("#94A3B8"))
            self.tools_table.setItem(row, 3, calls_item)

            desc_item = QtWidgets.QTableWidgetItem(t["desc"])
            desc_item.setToolTip(t["desc"])
            desc_item.setForeground(QtGui.QColor("#F8FAFC"))
            self.tools_table.setItem(row, 4, desc_item)

        self.tools_table.setColumnWidth(0, 45)
        self.tools_table.setColumnWidth(1, 275)
        self.tools_table.setColumnWidth(2, 110)
        self.tools_table.setColumnWidth(3, 60)

    def filter_tools_table(self):
        self.populate_tools_table()

    def populate_functions_combo(self):
        try:
            import idautils
            import ida_funcs
            if not self.func_combo:
                return
            self.func_combo.blockSignals(True)
            self.func_combo.clear()
            count = 0
            for ea in idautils.Functions():
                name = ida_funcs.get_func_name(ea)
                self.func_combo.addItem(f"{name} ({hex(ea)})", ea)
                if QtCore and QtGui:
                    cnt = self.func_combo.count()
                    self.func_combo.setItemData(cnt - 1, QtGui.QColor("#F8FAFC"), QtCore.Qt.ForegroundRole)
                count += 1
                if count >= 800:
                    break
            self.func_combo.blockSignals(False)
        except Exception:
            pass

    def refresh_cursor_info(self, ea=None):
        try:
            func_info = None
            if "McpDashboardState" in globals():
                func_info = McpDashboardState.get_current_function_details(ea)
            if not func_info:
                return

            if self.func_title_lbl:
                self.func_title_lbl.setText(McpDashboardState.t("func_title", name=func_info["name"]))
            if self.func_meta_lbl:
                self.func_meta_lbl.setText(McpDashboardState.t("func_range", start=func_info["start"], end=func_info["end"], size=func_info["size"]))
            
            if self.cff_badge:
                self.cff_badge.setVisible(func_info.get("has_cff", False))

            if self.code_viewer:
                self.code_viewer.setText(func_info.get("pseudocode", "No pseudocode available"))

            if self.func_combo and "start" in func_info:
                try:
                    f_start = int(func_info["start"], 16)
                    idx = self.func_combo.findData(f_start)
                    if idx >= 0 and self.func_combo.currentIndex() != idx:
                        self.func_combo.blockSignals(True)
                        self.func_combo.setCurrentIndex(idx)
                        self.func_combo.blockSignals(False)
                except Exception:
                    pass

            self.populate_xrefs(func_info["start"])
        except Exception:
            pass

    def populate_xrefs(self, start_ea_str):
        try:
            import idaapi
            import ida_xref
            import ida_funcs
            ea = int(start_ea_str, 16)
            
            if self.callers_list:
                self.callers_list.clear()
                xref = ida_xref.get_first_cref_to(ea)
                count = 0
                while xref != idaapi.BADADDR:
                    fn = ida_funcs.get_func_name(xref)
                    self.callers_list.addItem(f"{fn} ({hex(xref)})")
                    count += 1
                    xref = ida_xref.get_next_cref_to(ea, xref)
                if hasattr(self, "callers_header") and self.callers_header:
                    self.callers_header.setText(McpDashboardState.t("callers_header", count=count))

            if self.callees_list:
                self.callees_list.clear()
                func = ida_funcs.get_func(ea)
                count = 0
                if func:
                    for item in range(func.start_ea, func.end_ea, 4):
                        cref = ida_xref.get_first_cref_from(item)
                        while cref != idaapi.BADADDR:
                            fn = ida_funcs.get_func_name(cref)
                            if fn:
                                self.callees_list.addItem(f"{fn} ({hex(cref)})")
                                count += 1
                            cref = ida_xref.get_next_cref_from(item, cref)
                if hasattr(self, "callees_header") and self.callees_header:
                    self.callees_header.setText(McpDashboardState.t("callees_header", count=count))
        except Exception:
            pass

    def on_xref_item_clicked(self, item):
        try:
            import ida_kernwin
            text = item.text()
            m = re.search(r"\((0x[0-9a-fA-F]+)\)", text)
            if m:
                target_ea = int(m.group(1), 16)
                ida_kernwin.jumpto(target_ea)
        except Exception:
            pass

    def on_combo_func_selected(self, index):
        try:
            if not self.func_combo or index < 0:
                return
            ea = self.func_combo.currentData()
            if ea is not None:
                if "McpDashboardState" in globals():
                    McpDashboardState.current_selected_func = ea
                import ida_kernwin
                ida_kernwin.jumpto(ea)
                self.refresh_cursor_info(ea)
        except Exception:
            pass

    def on_jump_to_current(self):
        try:
            import ida_kernwin
            func_info = None
            if "McpDashboardState" in globals():
                func_info = McpDashboardState.get_current_function_details()
            if func_info and "start" in func_info:
                ea = int(func_info["start"], 16)
                ida_kernwin.jumpto(ea)
                if "McpDashboardState" in globals():
                    McpDashboardState.log_event("ui_action", f"Jumped in IDA to {func_info['name']}")
        except Exception:
            pass

    def on_deobfuscate_clicked(self):
        analysis_html = "<div style='color: #FB7185;'>Deobfuscation analyzer not available in mock mode</div>"
        if "McpDashboardState" in globals():
            analysis_html = McpDashboardState.analyze_function_deobfuscation()
        if self.ai_output_view:
            self.ai_output_view.setHtml(analysis_html)
        if "McpDashboardState" in globals():
            McpDashboardState.log_event("agent_call", "Executed Deobfuscation Analysis")

    def on_suggest_renames_clicked(self):
        self.on_deobfuscate_clicked()
        if self.ai_output_view:
            notice = McpDashboardState.t("click_apply_renames")
            self.ai_output_view.append(f"<br/><div style='background: #171D28; border: 1px solid #8B5CF6; padding: 8px; border-radius: 6px; color: #38BDF8;'>{notice}</div>")

    def on_apply_renames_clicked(self):
        ok = False
        msg = McpDashboardState.t("no_renames")
        if "McpDashboardState" in globals():
            ok, msg = McpDashboardState.apply_renames_to_ida()
        if self.ai_output_view:
            color = "#34D399" if ok else "#FB7185"
            self.ai_output_view.append(f"<br/><div style='color: {color}; font-weight: 700; padding: 6px; background-color: #171D28; border-radius: 4px;'>{msg}</div>")

    def on_submit_prompt(self):
        if not self.prompt_input:
            return
        q = self.prompt_input.text().strip()
        if not q:
            return
        self.prompt_input.clear()
        active_agent = "Antigravity"
        if "McpDashboardState" in globals():
            active_agent = McpDashboardState.get_active_agent_key()
        
        if self.ai_output_view:
            self.ai_output_view.append(f"<hr style='border: 1px solid #283142;'/><div style='color: #8B5CF6; font-weight: 700;'>User Query to {active_agent}: {q}</div>")
            ans = "Direct query requires active IDA session."
            if "McpDashboardState" in globals():
                ans = McpDashboardState.answer_user_query(q)
            self.ai_output_view.append(f"<div style='margin-top: 6px; color: #E8EDF5; line-height: 140%;'>{ans}</div>")
        if "McpDashboardState" in globals():
            McpDashboardState.log_event("ui_action", f"Agent Query to {active_agent}: {q[:50]}")

    def on_copy_c_code(self):
        func_info = None
        if "McpDashboardState" in globals():
            func_info = McpDashboardState.get_current_function_details()
        if func_info and func_info.get("pseudocode") and QtWidgets:
            QtWidgets.QApplication.clipboard().setText(func_info["pseudocode"])
            if hasattr(self, "c_status_lbl") and self.c_status_lbl:
                self.c_status_lbl.setText(McpDashboardState.t("c_copied"))
                if QtCore:
                    QtCore.QTimer.singleShot(3000, lambda: self.c_status_lbl.setText(""))
            if "McpDashboardState" in globals():
                McpDashboardState.log_event("ui_action", "Copied C pseudocode to clipboard")

    def on_send_func(self):
        ok = False
        name = ""
        active_agent = "Antigravity"
        if "McpDashboardState" in globals():
            ok, name = McpDashboardState.export_current_function_for_agent()
            active_agent = McpDashboardState.get_active_agent_key()
        if ok:
            if "McpDashboardState" in globals():
                McpDashboardState.log_event("ui_action", f"Exported function '{name}' for {active_agent} to clipboard")
            if self.ai_output_view:
                self.ai_output_view.append(f"<div style='color: #34D399; font-weight: 600;'>{McpDashboardState.t('c_saved', name=name)}</div>")
            if hasattr(self, "c_status_lbl") and self.c_status_lbl:
                self.c_status_lbl.setText(McpDashboardState.t("c_saved", name=name))
                if QtCore:
                    QtCore.QTimer.singleShot(3000, lambda: self.c_status_lbl.setText(""))
        else:
            if self.ai_output_view:
                self.ai_output_view.append(f"<div style='color: #FB7185; font-weight: 600;'>Failed to export: {name}</div>")

    def on_log_filter_changed(self, idx):
        if idx >= 0 and self.log_filter_combo:
            self.active_log_filter = self.log_filter_combo.itemData(idx)
            self.rebuild_log_view()

    def on_clear_log(self):
        if self.log_text:
            self.log_text.clear()
        if "McpDashboardState" in globals():
            McpDashboardState.event_history.clear()

    def rebuild_log_view(self):
        if not self.log_text:
            return
        state_cls = globals().get("McpDashboardState") or getattr(sys.modules.get(__name__), "McpDashboardState", None)
        if not state_cls:
            return
        self.log_text.clear()
        for record in state_cls.event_history:
            self.append_log_safe(record)

    def append_log_safe(self, record):
        if not self.log_text or not QtCore:
            return
        if self.active_log_filter != "ALL" and record.get("type") != self.active_log_filter:
            return
        colors = {
            "agent_call": "#38BDF8",
            "agent_success": "#34D399",
            "agent_error": "#FB7185",
            "ui_action": "#FBBF24"
        }
        color = colors.get(record.get("type"), "#CBD5E1")
        msg = record.get("message", "").replace("<", "&lt;").replace(">", "&gt;")
        line = f"<span style='color: #94A3B8;'>[{record.get('time', '')}]</span> <span style='color: {color}; font-weight: 600;'>{msg}</span>"
        if QtCore.QThread.currentThread() == self.log_text.thread():
            self.log_text.append(line)
        else:
            QtCore.QMetaObject.invokeMethod(self.log_text, "append", QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, line))

ReaAgentTab = McpDashboardTab

_UI_Hooks_Base = getattr(ida_kernwin, "UI_Hooks", object)

class McpUIHooks(_UI_Hooks_Base):
    def ready_to_run(self):
        show_mcp_dashboard()

    def database_inited(self, is_new_database, idc_script):
        if QtCore and QtWidgets:
            QtCore.QTimer.singleShot(400, show_mcp_dashboard)
        else:
            show_mcp_dashboard()

    def screen_ea_changed(self, ea, prev_ea):
        McpDashboardState.on_ea_changed(ea)

    def view_curpos_changed(self, view):
        cur_ea = ida_kernwin.get_screen_ea()
        McpDashboardState.on_ea_changed(cur_ea)

ReaUIHooks = McpUIHooks

def show_mcp_dashboard():
    def _do():
        try:
            if not QtWidgets:
                return 0
            existing = ida_kernwin.find_widget("MCP Dashboard")
            if existing:
                ida_kernwin.activate_widget(existing, True)
                if hasattr(McpDashboardState.tab_instance, "refresh_all"):
                    McpDashboardState.tab_instance.refresh_all()
                return 1
            McpDashboardState.tab_instance = McpDashboardTab()
            flags = ida_kernwin.PluginForm.WOPN_RESTORE | ida_kernwin.PluginForm.WOPN_DP_RIGHT
            res = McpDashboardState.tab_instance.Show("MCP Dashboard", options=flags)
            if hasattr(McpDashboardState.tab_instance, "refresh_all"):
                McpDashboardState.tab_instance.refresh_all()
            return 1
        except Exception:
            return 0
    return ida_kernwin.execute_sync(_do, ida_kernwin.MFF_FAST)

show_rea_tab = show_mcp_dashboard

class OpenMcpActionHandler(ida_kernwin.action_handler_t):
    def __init__(self):
        super().__init__()

    def activate(self, ctx):
        show_mcp_dashboard()
        return 1

    def update(self, ctx):
        return ida_kernwin.AST_ENABLE_ALWAYS

OpenReaActionHandler = OpenMcpActionHandler

def register_mcp_actions():
    try:
        action_name = "mcp:open_dashboard"
        action_desc = ida_kernwin.action_desc_t(
            action_name,
            "MCP Dashboard",
            OpenMcpActionHandler(),
            "Ctrl-Alt-M",
            "Open MCP Dashboard dockable panel",
            -1
        )
        ida_kernwin.register_action(action_desc)
        ida_kernwin.attach_action_to_menu("View/Open subviews/", action_name, ida_kernwin.SETMENU_APP)
    except Exception:
        pass

register_rea_action = register_mcp_actions

class MCP(ida_idaapi.plugin_t):
    flags = ida_idaapi.PLUGIN_FIX
    comment = "IDA Pro MCP Server Dashboard & AI Agent Bridge"
    help = "MCP Dashboard"
    wanted_name = "MCP Dashboard"
    wanted_hotkey = "Ctrl-Alt-M"

    def init(self):
        register_mcp_actions()
        if not (McpDashboardState.server and McpDashboardState.server.running):
            self.server = Server()
            self.server.start()
            McpDashboardState.server = self.server
        else:
            self.server = McpDashboardState.server

        try:
            self.ui_hooks = McpUIHooks()
            self.ui_hooks.hook()
            McpDashboardState.ui_hooks = self.ui_hooks
        except Exception:
            pass

        if QtCore and QtWidgets:
            QtCore.QTimer.singleShot(500, show_mcp_dashboard)
        else:
            show_mcp_dashboard()

        print("[MCP Dashboard] Plugin loaded successfully (http://127.0.0.1:13337/mcp)")
        return ida_idaapi.PLUGIN_KEEP

    def run(self, arg):
        show_mcp_dashboard()

    def term(self):
        if hasattr(self, "ui_hooks") and self.ui_hooks:
            try:
                self.ui_hooks.unhook()
            except Exception:
                pass
        if self.server:
            self.server.stop()

def PLUGIN_ENTRY():
    return MCP()

try:
    if "ida_idaapi" in sys.modules or "idaapi" in sys.modules:
        register_mcp_actions()
        if not (McpDashboardState.server and McpDashboardState.server.running):
            _direct_srv = Server()
            _direct_srv.start()
            McpDashboardState.server = _direct_srv
        if QtCore and QtWidgets:
            QtCore.QTimer.singleShot(400, show_mcp_dashboard)
        else:
            show_mcp_dashboard()
        print("[MCP Dashboard] Auto-loaded MCP Server and Dashboard!")
except Exception as _ex:
    print(f"[MCP Dashboard] Startup notification: {_ex}")
