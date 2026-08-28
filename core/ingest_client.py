"""POST `Document`s to the Go API's v2 ingest endpoint (CONTRACT.md section 6).

`POST <api_url>/v2/ingest` with header `X-Ingest-Token`, retrying connection
errors and 5xx responses with exponential backoff; 4xx responses (bad auth,
bad body) are not retried since a retry cannot fix them.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Iterable, Optional

import requests

from core.schema import Document

log = logging.getLogger(__name__)


class IngestError(Exception):
    """Raised when posting documents to the ingest API fails, either because
    a non-retryable (4xx) response was received or all retries were
    exhausted."""


def post_documents(
    documents: Iterable[Document],
    *,
    source: str,
    api_url: str,
    token: Optional[str] = None,
    dry_run: bool = False,
    check_existing: bool = True,
    update_players: bool = True,
    timeout: int = 60,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """POST the CONTRACT.md section 6 body to `<api_url>/v2/ingest`.

    Returns the parsed JSON response body (contains `runId`, `status`,
    `documentCount` on success). Raises `IngestError` if the token is
    missing, a 4xx is returned, or all retries are exhausted.
    """
    if token is None:
        token = os.environ.get("INGEST_TOKEN")
    if not token:
        raise IngestError(
            "no ingest token provided: set INGEST_TOKEN in source-data/.env "
            "(or export it), and make sure the same value is set for the API - "
            "docker-compose passes it through as INGEST_TOKEN. The API replies "
            "503 when it has no token configured and 401 when it does not match."
        )

    body = {
        "source": source,
        "dryRun": dry_run,
        "checkExisting": check_existing,
        "updatePlayers": update_players,
        "documents": [d.model_dump(by_alias=True, mode="json") for d in documents],
    }

    url = api_url.rstrip("/") + "/v2/ingest"
    headers = {"X-Ingest-Token": token}

    last_error: Optional[BaseException] = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, json=body, headers=headers, timeout=timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_error = exc
            log.warning(
                "ingest POST connection error (attempt %d/%d) to %s: %s",
                attempt,
                max_retries,
                url,
                exc,
            )
        else:
            if response.status_code < 300:
                return response.json()

            if 500 <= response.status_code < 600:
                last_error = IngestError(
                    f"server error {response.status_code}: {response.text}"
                )
                log.warning(
                    "ingest POST server error (attempt %d/%d) to %s: %s",
                    attempt,
                    max_retries,
                    url,
                    last_error,
                )
            else:
                # 4xx: not retryable (bad token, bad body, source mismatch, ...).
                log.error(
                    "ingest POST to %s failed with non-retryable status %d: %s",
                    url,
                    response.status_code,
                    response.text,
                )
                raise IngestError(
                    f"ingest POST failed with {response.status_code}: {response.text}"
                )

        if attempt < max_retries:
            backoff = 2 ** (attempt - 1)
            time.sleep(backoff)

    raise IngestError(
        f"ingest POST to {url} failed after {max_retries} attempts: {last_error}"
    )
