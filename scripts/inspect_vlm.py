"""Print raw model responses only for an explicitly approved non-sensitive fixture."""

import argparse
import json
from pathlib import Path
from time import perf_counter

from backend.config import Settings
from backend.images import normalize
from backend.model_diagnostics import inspect_fixture
from backend.models import OllamaModels
from backend.workflow import Workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--non-sensitive-fixture", action="store_true", required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    if settings.mode != "real":
        parser.error("Set APP_MODE=real and configure local Ollama first.")
    photo = normalize(args.fixture.read_bytes())
    workflow = Workflow(settings, OllamaModels(settings))
    start = perf_counter()
    with inspect_fixture() as responses:
        result = workflow.run(
            photos=[photo], previous={}, correction=None, edited_fields=[], identifiers=[], attempt=0, logs=[],
            intake={"region": "시험 지역", "location": "시험 안내소", "institution": "시험 보관기관", "memo": "", "quantity": 1},
        )
    keys = ("extraction", "observation", "item", "evidence", "findings", "privacy", "notice_privacy", "decision",
            "analysis_status", "questions", "failure", "logs")
    print(json.dumps({"seconds": round(perf_counter() - start, 2), "responses": responses,
                      **{key: result[key] for key in keys if key in result}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
