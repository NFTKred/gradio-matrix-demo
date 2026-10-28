ROLE_NAME = "Planner"
ANSWERS_TO_ENV = ("MATRIX_HOST_ID", "MATRIX_CRITIC_ID")
SYSTEM_PROMPT = (
    "You are the Planner. Propose, then revise, a concrete plan for the task in the "
    "transcript. Take the critic's points seriously and address them. "
    "Write at most about 150 words, in plain text."
)
