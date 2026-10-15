from typing import Any

def ok(data: Any = None, message: str = "ok") -> dict:
    return {"code": 0, "message": message, "data": data}