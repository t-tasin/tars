"""Tests for the multi-agent coding pipeline."""

from __future__ import annotations

from agents.coding_pipeline import CodingPipeline, PipelineTask

# ---------------------------------------------------------------------------
# TestTopologicalBatches
# ---------------------------------------------------------------------------


class TestTopologicalBatches:
    """Tests for CodingPipeline._topological_batches."""

    def _make_pipeline(self) -> CodingPipeline:
        """Create a CodingPipeline without calling __init__."""
        return CodingPipeline.__new__(CodingPipeline)

    def test_independent_tasks_single_batch(self) -> None:
        """Three tasks with no dependencies should be in one batch."""
        pipeline = self._make_pipeline()
        tasks = [
            PipelineTask(id=1, description="Task A"),
            PipelineTask(id=2, description="Task B"),
            PipelineTask(id=3, description="Task C"),
        ]
        batches = pipeline._topological_batches(tasks)

        assert len(batches) == 1
        assert len(batches[0]) == 3
        batch_ids = {t.id for t in batches[0]}
        assert batch_ids == {1, 2, 3}

    def test_sequential_dependencies(self) -> None:
        """A -> B -> C should produce three batches of one task each."""
        pipeline = self._make_pipeline()
        tasks = [
            PipelineTask(id=1, description="Task A"),
            PipelineTask(id=2, description="Task B", depends_on=[1]),
            PipelineTask(id=3, description="Task C", depends_on=[2]),
        ]
        batches = pipeline._topological_batches(tasks)

        assert len(batches) == 3
        assert batches[0][0].id == 1
        assert batches[1][0].id == 2
        assert batches[2][0].id == 3

    def test_mixed_dependencies(self) -> None:
        """A and B independent, C depends on both -> 2 batches."""
        pipeline = self._make_pipeline()
        tasks = [
            PipelineTask(id=1, description="Task A"),
            PipelineTask(id=2, description="Task B"),
            PipelineTask(id=3, description="Task C", depends_on=[1, 2]),
        ]
        batches = pipeline._topological_batches(tasks)

        assert len(batches) == 2
        first_ids = {t.id for t in batches[0]}
        assert first_ids == {1, 2}
        assert batches[1][0].id == 3


# ---------------------------------------------------------------------------
# TestParseJson
# ---------------------------------------------------------------------------


class TestParseJson:
    """Tests for CodingPipeline._parse_json."""

    def test_plain_json(self) -> None:
        """Plain JSON string parses correctly."""
        result = CodingPipeline._parse_json('{"approved": true, "issues": []}')
        assert result["approved"] is True
        assert result["issues"] == []

    def test_json_in_markdown_fence(self) -> None:
        """JSON inside markdown code fences is extracted."""
        text = '```json\n{"approved": true, "issues": []}\n```'
        result = CodingPipeline._parse_json(text)
        assert result["approved"] is True

    def test_json_embedded_in_text(self) -> None:
        """JSON embedded within prose is extracted."""
        text = 'Here is the review result:\n{"approved": false, "issues": ["bug found"]}\nEnd of review.'
        result = CodingPipeline._parse_json(text)
        assert result["approved"] is False
        assert result["issues"] == ["bug found"]

    def test_invalid_json_returns_fallback(self) -> None:
        """Unparseable text returns a fallback dict with approved=False."""
        result = CodingPipeline._parse_json("this is not json at all!!!")
        assert result["approved"] is False
        assert len(result["issues"]) > 0
