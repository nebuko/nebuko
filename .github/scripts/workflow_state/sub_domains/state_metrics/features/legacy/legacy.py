from typing import Optional, Any
import json
import re

from ...state_metrics import StateMetrics

def legacy_metrics_to_state_metrics(metrics_json_str: str) -> Optional[StateMetrics]:
    """Convert a legacy <!-- agent-metrics: {...} --> JSON string to StateMetrics."""
    try:
        data = json.loads(metrics_json_str)
        return StateMetrics.from_full_metrics_dict(data)
    except (json.JSONDecodeError, Exception):
        return None


LEGACY_METRICS_RE = re.compile(r"<!--\s*agent-metrics:\s*(.*?)\s*-->")


def extract_legacy_metrics(body: str) -> dict[str, Any]:
    """Extract metrics JSON from a legacy HTML comment marker."""
    if not body:
        return {}
    match = LEGACY_METRICS_RE.search(body)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}
    return {}
