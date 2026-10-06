"""Result parser extracted from cbangera2/robinhood-mcp-cli (MIT).

Source: 82a7abdb8286c3a9d144ea624eb43cabf37c8272, rh_mcp_cli/client.py.
Local adaptation: reject isError before accepting structured/text content.
The upstream HTTP client and token storage are not used or shipped.
See licenses/cbangera2-MIT.txt and THIRD_PARTY.md.
"""
import json
from typing import Any
from .exceptions import MCPError


def _parse_result(result: Any) -> Any:
    # Local safety adaptation: successful HTTP/MCP transport is not tool success.
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        raise MCPError("broker tool returned an error")
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    content = getattr(result, "content", None)
    if content:
        for item in content:
            text = getattr(item, "text", None)
            if not text:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return result

