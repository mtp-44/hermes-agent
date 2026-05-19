from gateway.jira_mcp import _extract_issue_rows, _normalize_issue


def test_extract_issue_rows_supports_issues_payload():
    payload = {
        "issues": [
            {"key": "PROJ-1", "fields": {"summary": "One"}},
            {"key": "PROJ-2", "fields": {"summary": "Two"}},
        ]
    }

    rows = _extract_issue_rows(payload)

    assert len(rows) == 2
    assert rows[0]["key"] == "PROJ-1"


def test_normalize_issue_reads_nested_field_shape():
    issue = {
        "key": "PROJ-9",
        "fields": {
            "summary": "Harden Jira pull surface",
            "status": {"name": "In Progress"},
            "assignee": {"displayName": "Mark"},
            "priority": {"name": "High"},
        },
    }

    normalized = _normalize_issue(issue)

    assert normalized == {
        "key": "PROJ-9",
        "summary": "Harden Jira pull surface",
        "status": "In Progress",
        "assignee": "Mark",
        "priority": "High",
    }
