"""Centralized fallback message constants for the QA module.

Application consumers can maintain their own mapping table
to translate these keys into the target language.
"""


class FallbackMessage:
    FAILED_TO_GENERATE_ANSWER = "Failed to generate answer."
    NO_SUB_QUERY_ANSWERS = "No sub-query answers found."
    NO_RETRIEVAL_RESULTS = "No relevant retrieval results found."
    NO_INTERMEDIATE_STEPS = "No intermediate step information found."
