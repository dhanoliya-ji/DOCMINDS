"""The BUILT-IN prompt templates every organization starts with."""
from typing import Dict, List

BUILTIN_TEMPLATES: List[Dict[str, str]] = [
    {
        "key": "summarise-risks",
        "title": "Summarise risks",
        "body": (
            "Summarise the main risks, liabilities and obligations described in these "
            "documents. For each one give a short title, who it affects, and how serious "
            "it is, with a citation. Group them by theme and finish with the three that "
            "need attention first."
        ),
    },
    {
        "key": "extract-dates",
        "title": "Extract dates and deadlines",
        "body": (
            "List every date, deadline, notice period and renewal term mentioned in these "
            "documents as a table with the columns: date or period, what it applies to, "
            "and source. Order it chronologically and cite each row."
        ),
    },
    {
        "key": "compare-clauses",
        "title": "Compare clauses",
        "body": (
            "Compare how these documents handle termination, liability, payment terms and "
            "confidentiality. For each topic, say what each document states, where they "
            "differ, and cite both sides. Say plainly when a document does not cover a topic."
        ),
    },
    {
        "key": "key-figures",
        "title": "Key figures",
        "body": (
            "Pull out the key numeric figures in these documents (amounts, percentages, "
            "quantities, limits) as a table with what each figure measures and its source. "
            "If several figures can be compared, add a chart."
        ),
    },
]


def builtin_id(key: str) -> str:
    return f"builtin:{key}"
