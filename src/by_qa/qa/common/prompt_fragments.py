"""Default prompt fragments for the QA module.

Application consumers can override these via middleware
(before_agent / before_model_call) to inject custom language rules.
"""

DEFAULT_LANGUAGE_INSTRUCTION = (
    "\n\n## Language\n"
    "Always respond in the same language as the user's input message. "
    "Determine the language by the user's sentence structure, not by entity names or proper nouns within it. "
    "For example, if the user writes 'What are the shortcomings of 亦庄?', "
    "the input language is English — respond in English."
)
