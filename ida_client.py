import json
import urllib.request

class IdaClient:
    def __init__(self, endpoint="http:" + "/" + "/" + "127.0.0.1:13337/mcp", timeout=60):
        self.endpoint = endpoint
        self.timeout = timeout
        self.req_id = 0

    def call(self, method, params=None):
        if params is None:
            params = []
        self.req_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self.req_id,
            "method": method,
            "params": params
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return res.get("result")
        except Exception:
            return None

    def get_metadata(self):
        return self.call("get_metadata")

    def get_current_address(self):
        return self.call("get_current_address")

    def get_current_function(self):
        return self.call("get_current_function")

    def get_function(self, address_or_name):
        res = self.call("get_function_by_address", [address_or_name])
        if not res:
            res = self.call("get_function_by_name", [address_or_name])
        return res

    def disassemble(self, address_or_name):
        return self.call("disassemble_function", [address_or_name])

    def decompile(self, address_or_name):
        return self.call("decompile_function", [address_or_name])

    def get_xrefs(self, address):
        return self.call("get_xrefs_to", [address])

    def set_comment(self, address, comment):
        return self.call("set_comment", [address, comment])

    def rename_local_variable(self, func_addr, old_name, new_name):
        return self.call("rename_local_variable", [func_addr, old_name, new_name])

    def rename_function(self, old_name_or_addr, new_name):
        return self.call("rename_function", [old_name_or_addr, new_name])

    def read_qword(self, address):
        return self.call("data_read_qword", [address])
