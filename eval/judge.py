"""LLM-as-judge scoring for QA eval."""

import json
import re

from eval.models import JudgeVerdict

JUDGE_PROMPT = """\
You are evaluating whether a model's answer is correct compared to a reference answer.

Question: {question}

Reference Answer: {ground_truth}

Model Answer: {answer}

Is the model's answer factually correct based on the reference answer? \
The model's answer does not need to be word-for-word identical, \
but it must convey the same factual information.

Respond with JSON only:
{{"score": 0 or 1, "reasoning": "brief explanation"}}"""


def _parse_judge_response(raw: str) -> tuple[int, str] | None:
    """Parse the judge's JSON response. Returns (score, reasoning) or None."""
    # Try direct JSON parse first
    try:
        data = json.loads(raw)
        return int(data["score"]), str(data.get("reasoning", ""))
    except (json.JSONDecodeError, KeyError, ValueError):
        pass

    # Try extracting JSON block from markdown code fences
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            return int(data["score"]), str(data.get("reasoning", ""))
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    # Try finding any JSON object in the response
    match = re.search(r"\{[^{}]*\"score\"[^{}]*\}", raw)
    if match:
        try:
            data = json.loads(match.group(0))
            return int(data["score"]), str(data.get("reasoning", ""))
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    return None


async def judge(
    llm_service,
    question: str,
    ground_truth: str,
    answer: str,
    model_type: str = "generator",
) -> JudgeVerdict:
    """Score a single answer using an LLM judge.

    Args:
        llm_service: by_qa LLMService instance.
        question: The original question.
        ground_truth: The reference answer.
        answer: The model's answer to evaluate.
        model_type: LLM model role to use (default: "generator").

    Returns:
        JudgeVerdict with score, reasoning, and judge model name.
    """
    prompt = JUDGE_PROMPT.format(
        question=question,
        ground_truth=ground_truth,
        answer=answer,
    )
    messages = [{"role": "user", "content": prompt}]

    raw = await llm_service.generate(messages, model_type=model_type, json_mode=True)

    parsed = _parse_judge_response(raw)
    if parsed is not None:
        score, reasoning = parsed
        config = await llm_service.get_model_config(model_type)
        return JudgeVerdict(
            query_id="",  # caller fills in
            score=score,
            reasoning=reasoning,
            judge_model=config.model_name,
        )

    # Retry once without json_mode (some models handle plain text better)
    raw = await llm_service.generate(messages, model_type=model_type, json_mode=False)
    parsed = _parse_judge_response(raw)
    if parsed is not None:
        score, reasoning = parsed
        config = await llm_service.get_model_config(model_type)
        return JudgeVerdict(
            query_id="",
            score=score,
            reasoning=reasoning,
            judge_model=config.model_name,
        )

    # Still failed — mark as unscored
    config = await llm_service.get_model_config(model_type)
    return JudgeVerdict(
        query_id="",
        score=-1,  # sentinel for unscored
        reasoning=f"Judge failed to produce valid JSON. Raw response: {raw[:200]}",
        judge_model=config.model_name,
    )
