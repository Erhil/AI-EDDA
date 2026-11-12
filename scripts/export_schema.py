import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.path.insert(0, str(ROOT / "src"))

    from pydantic import BaseModel

    from ai_edda.application import CommandEnvelope, ProjectView
    from ai_edda.arrangement_domain import (
        ArrangementRequest,
        ChartDraft,
        GeneratorCapabilities,
        MusicalPlan,
    )

    class UiContract(BaseModel):
        project_view: ProjectView
        command_envelope: CommandEnvelope
        arrangement_request: ArrangementRequest
        chart_draft: ChartDraft
        generator_capabilities: GeneratorCapabilities
        musical_plan: MusicalPlan

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "web" / "schema" / "ui-contract.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(UiContract.model_json_schema(), indent=2) + "\n"
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != content:
            print(f"Schema differs: {args.output}", file=sys.stderr)
            return 1
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
