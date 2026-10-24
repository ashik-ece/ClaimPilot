"""Pipeline worker: python -m app.pipeline.worker [--once]"""

import argparse
import logging
import time

from qdrant_client import QdrantClient

from app.config import settings
from app.db import SessionLocal
from app.obs import configure_logging
from app.pipeline import jobs
from app.pipeline.llm import get_llm_client
from app.pipeline.policy_index import FastEmbedder, PolicyIndex
from app.pipeline.stages import build_handlers, flag_for_review
from app.storage import S3Storage

log = logging.getLogger("claimpilot.worker")


def main() -> None:
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="drain the queue, then exit")
    args = ap.parse_args()
    index = PolicyIndex(QdrantClient(url=settings.qdrant_url), FastEmbedder())
    handlers = build_handlers(S3Storage(), get_llm_client(), index)
    log.info("worker started (llm mode %s)", settings.llm_mode)
    while True:
        worked = jobs.run_once(SessionLocal, handlers, on_exhausted=flag_for_review)
        if not worked:
            if args.once:
                return
            time.sleep(2)


if __name__ == "__main__":
    main()
