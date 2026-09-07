"""Compatibility mappings for durable File Build states."""

from by_qa.knowledge_base.build_status import legacy_build_status, legacy_build_step


def test_durable_statuses_map_to_legacy_contract():
    assert legacy_build_status("pending") == "running"
    assert legacy_build_status("running") == "running"
    assert legacy_build_status("succeeded") == "complete"
    assert legacy_build_status("skipped") == "skipped"
    assert legacy_build_status("unsupported") == "unsupported"


def test_durable_stages_map_to_legacy_steps():
    assert (
        legacy_build_step(
            status="running", current_step="markdown", current_stage="embedding"
        )
        == "vectorizing"
    )
    assert (
        legacy_build_step(
            status="succeeded", current_step="vectorizing", current_stage="committing"
        )
        == "complete"
    )
