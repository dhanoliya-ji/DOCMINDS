"""
prompt_library.py  (service)
============================
WHAT THIS FILE DOES
-------------------
The BUILT-IN prompt templates every organization starts with. Saved
templates (personal and org-shared) are rows in prompt_templates
(models/prompt.py); these are code, so they exist from an organization's
first minute and improve with an upgrade, no data migration needed.

Each has a stable `key`. The API returns it as the id "builtin:<key>", and
the frontend uses the key to show a translated title and body
(lib/i18n/collab.ts) -- falling back to the English text here.

WHAT MAKES A GOOD BUILT-IN
--------------------------
A task people repeat against business documents, phrased so the answer is
easy to check: it asks for a list, asks for the citation on every item, and
says what to do when the documents are silent.
"""

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
