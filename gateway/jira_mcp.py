from __future__ import annotations

import json
import os
from typing import Any

import httpx

from hermes_cli.config import load_config


class JiraConfigError(RuntimeError):
    """Raised when Hermes is missing a usable Jira MCP configuration."""


_JIRA_SERVER_CANDIDATES = ("jira", "atlassian", "atlassian_rovo")
_JIRA_TOOL_DEFAULT = "_searchjiraissuesusingjql"
_JIRA_JQL_DEFAULT = "sprint in openSprints() ORDER BY Rank ASC"
_JIRA_FIELDS_DEFAULT = ("summary", "status", "assignee", "priority")


def _parse_jsonrpc_http_body(raw_text: str) -> dict[str, Any]:
    stripped = raw_text.strip()
    if not stripped:
        raise RuntimeError("Empty HTTP response body from Jira MCP.")

    if stripped.startswith("{"):
        return json.loads(stripped)

    if "data:" in stripped:
        data_lines = []
        for line in stripped.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        if not data_lines:
            raise RuntimeError(f"Could not find SSE data lines in response: {raw_text}")
        return json.loads("\n".join(data_lines))

    raise RuntimeError(f"Unsupported Jira MCP response format: {raw_text}")


def _extract_text_content(response_body: dict[str, Any]) -> str:
    result = response_body.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"Missing JSON-RPC result payload: {response_body}")

    content = result.get("content")
    if not isinstance(content, list) or not content:
        raise RuntimeError(f"Missing MCP content payload: {result}")

    first = content[0]
    if not isinstance(first, dict) or not isinstance(first.get("text"), str):
        raise RuntimeError(f"Missing MCP text content: {first}")

    return first["text"]


def _jira_server_candidates(servers: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    explicit = str(os.getenv("HERMES_JIRA_MCP_SERVER", "") or "").strip()
    names: list[str] = []
    if explicit:
        names.append(explicit)
    names.extend(name for name in _JIRA_SERVER_CANDIDATES if name not in names)
    names.extend(
        name
        for name in servers
        if isinstance(name, str)
        and name not in names
        and ("jira" in name.lower() or "atlassian" in name.lower())
    )
    return [
        (name, server)
        for name in names
        if isinstance((server := servers.get(name)), dict)
    ]


def _resolve_jira_server() -> dict[str, Any]:
    config = load_config()
    servers = config.get("mcp_servers") or {}
    if not isinstance(servers, dict):
        raise JiraConfigError("No MCP servers are configured.")

    for name, server in _jira_server_candidates(servers):
        url = str(server.get("url") or "").strip()
        if not url:
            continue

        cloud_id = str(server.get("cloudId") or server.get("cloud_id") or "").strip()
        if not cloud_id:
            raise JiraConfigError(
                f"The `{name}` MCP server is missing `cloudId` for Jira queries."
            )

        headers = server.get("headers") or {}
        if not isinstance(headers, dict):
            headers = {}

        normalized_headers = {
            str(key): str(value)
            for key, value in headers.items()
            if str(value).strip()
        }
        normalized_headers.setdefault("content-type", "application/json")
        normalized_headers.setdefault("accept", "application/json")

        return {
            "name": name,
            "url": url,
            "headers": normalized_headers,
            "cloud_id": cloud_id,
            "tool_name": str(server.get("current_sprint_tool") or server.get("tool_name") or _JIRA_TOOL_DEFAULT),
            "jql": str(server.get("current_sprint_jql") or _JIRA_JQL_DEFAULT),
        }

    raise JiraConfigError(
        "No HTTP Jira MCP server is configured. Add `mcp_servers.jira` or another Atlassian server with `url` and `cloudId`."
    )


async def call_jira_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    server = _resolve_jira_server()

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            server["url"],
            headers=server["headers"],
            json={
                "jsonrpc": "2.0",
                "id": f"jira-{tool_name}",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            },
        )
        response.raise_for_status()
        body = _parse_jsonrpc_http_body(response.text)

    result = body.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"Malformed Jira MCP response: {body}")

    payload_text = _extract_text_content(body)
    if result.get("isError") is True:
        raise RuntimeError(payload_text)

    try:
        return json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Jira MCP returned non-JSON text: {payload_text[:500]!r}") from exc


def _extract_issue_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    issues = payload.get("issues")
    if isinstance(issues, list):
        return [item for item in issues if isinstance(item, dict)]
    results = payload.get("results")
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    return []


def _issue_field(item: dict[str, Any], *path: str) -> str:
    current: Any = item
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    if isinstance(current, str):
        return current.strip()
    return ""


def _normalize_issue(item: dict[str, Any]) -> dict[str, str]:
    fields = item.get("fields")
    field_map = fields if isinstance(fields, dict) else item
    normalized = {
        "key": _issue_field(item, "key") or _issue_field(item, "issueKey"),
        "summary": _issue_field(field_map, "summary"),
        "status": _issue_field(field_map, "status", "name") or _issue_field(field_map, "status"),
        "assignee": _issue_field(field_map, "assignee", "displayName") or _issue_field(field_map, "assignee"),
        "priority": _issue_field(field_map, "priority", "name") or _issue_field(field_map, "priority"),
    }
    return normalized


def _matches_filter(issue: dict[str, str], query: str | None) -> bool:
    if not query:
        return True
    needle = query.strip().lower()
    if not needle:
        return True
    haystack = " ".join(issue.values()).lower()
    return needle in haystack


async def fetch_current_sprint_issues(
    *,
    query: str | None = None,
    limit: int = 10,
) -> list[dict[str, str]]:
    server = _resolve_jira_server()
    payload = await call_jira_tool(
        server["tool_name"],
        {
            "cloudId": server["cloud_id"],
            "jql": server["jql"],
            "fields": list(_JIRA_FIELDS_DEFAULT),
            "maxResults": max(limit * 3, limit),
        },
    )

    issues: list[dict[str, str]] = []
    for item in _extract_issue_rows(payload):
        issue = _normalize_issue(item)
        if not issue["key"] or not issue["summary"]:
            continue
        if _matches_filter(issue, query):
            issues.append(issue)
        if len(issues) >= limit:
            break
    return issues
