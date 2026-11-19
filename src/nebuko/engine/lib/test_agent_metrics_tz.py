from nebuko.engine.lib import agent_metrics as am


def test_naive_and_aware_start_times_compare():
    data = am.collect_issue_data(
        [], [{"start_time": "2026-01-01T00:00:00"}, {"start_time": "2026-01-01T01:00:00Z"}], "", None
    )
    assert data.first_agent_start.isoformat() == "2026-01-01T00:00:00+00:00"
