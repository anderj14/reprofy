"""Local HTTP intake API used by the Docker demo and connected applications."""

import hmac
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import ValidationError

from .bugs import BugReport
from .store import StoreError, YamlBugStore


app = FastAPI(title="Reprofy Local API", docs_url=None, redoc_url=None)


def _authorize(authorization: str | None) -> None:
    expected = os.environ.get("REPROFY_INGEST_TOKEN", "")
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid API token")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/reports", status_code=201)
async def create_report(payload: dict, authorization: str | None = Header(default=None)) -> dict:
    _authorize(authorization)
    try:
        report = BugReport.model_validate(payload, strict=True)
        project = Path(os.environ.get("REPROFY_PROJECT", ".")).resolve()
        bug = YamlBugStore(project).create(report)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors(include_input=False)) from error
    except (StoreError, OSError) as error:
        raise HTTPException(status_code=503, detail="Report storage is unavailable") from error
    return {"id": bug.id, "status": bug.status}
