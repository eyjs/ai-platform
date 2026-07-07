"""Ingest Worker 전용 엔트리포인트.

FastAPI 없이 순수 백그라운드 프로세스로 실행.
job_queue에서 SKIP LOCKED로 문서를 꺼내 파싱 → 임베딩 → VectorStore 저장.

사용법:
    python -m src.worker_main
"""

import asyncio
import base64
import os
import signal

from src.config import settings
from src.domain.models import UNPLACED_DOMAIN
from src.infrastructure.job_queue import JobQueue, QueueWorker
from src.infrastructure.providers.factory import ProviderFactory
from src.infrastructure.vector_store import VectorStore
from src.observability.logging import configure_logging, get_logger
from src.pipeline.ingest import IngestPipeline
from src.services.kms_sync import KmsSyncService

_json_logs = os.getenv("AIP_LOG_FORMAT", "json") == "json"
_log_level = os.getenv("AIP_LOG_LEVEL", "INFO")
configure_logging(level=_log_level, json_format=_json_logs)

logger = get_logger(__name__)


async def run_worker() -> None:
    """워커 프로세스 메인 루프."""
    logger.info(
        "worker_startup",
        mode=settings.provider_mode.value,
        database=settings.database_url.split("@")[-1],
    )

    # 1. DB 연결
    vector_store = VectorStore(settings.database_url)
    await vector_store.connect(
        min_size=settings.pg_pool_min,
        max_size=settings.pg_pool_max,
    )
    pool = vector_store.pool
    logger.info("worker_db_connected")

    # 2. Providers (Embedding + Parsing)
    provider_factory = ProviderFactory(settings)
    embedding_provider = provider_factory.get_embedding_provider()
    logger.info("worker_embedding_ready", type=type(embedding_provider).__name__)

    parsing_provider = provider_factory.get_parsing_provider()
    logger.info("worker_parser_ready", type=type(parsing_provider).__name__)

    # 3. Ingest Pipeline
    ingest_pipeline = IngestPipeline(
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        settings=settings,
        parsing_provider=parsing_provider,
    )

    # 4. KMS Sync Service
    kms_sync = KmsSyncService(
        settings=settings,
        vector_store=vector_store,
        ingest_pipeline=ingest_pipeline,
    )

    # 5. Job Queue + Workers
    job_queue = JobQueue(pool)

    async def ingest_handler(payload: dict) -> dict:
        # file_bytes는 base64로 전달됨 (API에서 인코딩)
        file_bytes = None
        if payload.get("file_base64"):
            file_bytes = base64.b64decode(payload["file_base64"])

        return await ingest_pipeline.ingest_text(
            title=payload["title"],
            content=payload.get("content"),
            domain_code=payload["domain_code"],
            file_name=payload.get("file_name"),
            security_level=payload.get("security_level", "PUBLIC"),
            source_url=payload.get("source_url"),
            metadata=payload.get("metadata", {}),
            file_bytes=file_bytes,
            mime_type=payload.get("mime_type"),
            external_id=payload.get("source_document_id"),
            # 구(舊) 작업 등 payload에 tenant 없으면 기본 테넌트로 (NOT NULL 4d 보장)
            tenant_id=payload.get("tenant_id") or "default",
        )

    async def kms_sync_handler(payload: dict) -> dict:
        action = payload.get("action", "")
        document_id = payload.get("document_id", "")

        if action == "sync":
            result = await kms_sync.sync_document(
                document_id, payload.get("data", {}), event=payload.get("event", ""),
            )
            # 배치된(placed) PDF 만 비동기 VLM 보강 큐에 적재(테이블 구조 복원).
            # 미배치(__unplaced__)는 승인 전이라 제외 — 승인 전 KMS 본문 변조·VLM 낭비 방지.
            # 별도 큐/워커라 fast 인제스트를 막지 않는다.
            is_pdf = result.get("mime_type") == "application/pdf"
            placed = result.get("domain_code") not in (None, UNPLACED_DOMAIN)
            if is_pdf and placed and result.get("status") != "skipped":
                # 중복 큐잉 방지: 같은 문서의 vlm 잡이 pending/processing 이면 스킵
                # (수십 분짜리 잡 — 중복 실행 시 KMS 본문 이중 교체 경합).
                existing = await job_queue.has_active_job("vlm_enhance", document_id)
                if existing:
                    logger.info(
                        "vlm_enhance_dedup", document_id=document_id, job_id=existing,
                    )
                else:
                    await job_queue.enqueue("vlm_enhance", {"document_id": document_id})
                    logger.info("vlm_enhance_enqueued", document_id=document_id)
            return result
        elif action == "delete":
            return await kms_sync.delete_document(document_id)
        elif action == "lifecycle":
            return await kms_sync.update_lifecycle(document_id, payload.get("status", ""))
        else:
            logger.warning("kms_sync_unknown_action", action=action)
            return {"status": "ignored", "action": action}

    async def vlm_enhance_handler(payload: dict) -> dict:
        # 비동기 VLM 보강: fast 결과를 기다리지 않고 백그라운드로 테이블 보정 → KMS 본문 교체.
        # payload 전체를 넘긴다 — QueueWorker 가 주입한 job_attempts/job_max_attempts 로
        # enhance_document 가 "마지막 시도"를 판단한다(재시도 시맨틱).
        return await kms_sync.enhance_document(payload.get("document_id", ""), payload)

    ingest_worker = QueueWorker(
        queue=job_queue,
        queue_name="ingest",
        handler=ingest_handler,
        poll_interval=2.0,
        max_concurrent=3,
    )

    kms_sync_worker = QueueWorker(
        queue=job_queue,
        queue_name="kms_sync",
        handler=kms_sync_handler,
        poll_interval=2.0,
        max_concurrent=2,
    )

    # VLM 보강 전용 워커: full-page VLM 은 문서당 수십 분 → 동시성 1로 제한해
    # 일반 인제스트(ingest/kms_sync)를 굶기지 않는다. 완료 시 KMS 본문 교체.
    vlm_enhance_worker = QueueWorker(
        queue=job_queue,
        queue_name="vlm_enhance",
        handler=vlm_enhance_handler,
        poll_interval=5.0,
        max_concurrent=1,
    )

    # 6. Stale job 주기적 정리 (lease 기반 — job_queue.cleanup_stale 참조).
    # 첫 스윕을 sleep 없이 즉시 실행: 재기동 직후 이전 프로세스의 고아 잡을
    # 바로 회수한다 (기존 300s 지연은 인시던트 복구를 그만큼 늦췄다).
    async def _periodic_cleanup():
        while True:
            try:
                recovered = await job_queue.cleanup_stale(stale_seconds=600)
                if recovered > 0:
                    logger.info("worker_stale_recovered", count=recovered)
            except Exception as e:
                logger.warning("worker_cleanup_failed", error=str(e))
            await asyncio.sleep(120)

    cleanup_task = asyncio.create_task(_periodic_cleanup())

    # 7. Graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler():
        logger.info("worker_shutdown_signal")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # 8. 워커 시작 + 종료 대기
    ingest_task = asyncio.create_task(ingest_worker.start())
    kms_sync_task = asyncio.create_task(kms_sync_worker.start())
    vlm_enhance_task = asyncio.create_task(vlm_enhance_worker.start())
    logger.info("workers_ready", queues=["ingest", "kms_sync", "vlm_enhance"])

    await shutdown_event.wait()

    # 9. 정리
    logger.info("worker_draining")
    await ingest_worker.stop(timeout=30.0)
    await kms_sync_worker.stop(timeout=30.0)
    await vlm_enhance_worker.stop(timeout=30.0)
    for task in (ingest_task, kms_sync_task, vlm_enhance_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    if hasattr(embedding_provider, "close"):
        try:
            await embedding_provider.close()
        except Exception:
            pass

    await vector_store.close()
    logger.info("worker_shutdown_complete")


if __name__ == "__main__":
    asyncio.run(run_worker())
