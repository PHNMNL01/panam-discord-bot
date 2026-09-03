"""Command-line host for the bounded synchronous DL-2.2 Worker."""

import argparse
import signal
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from .command_queue import DurableCommandQueueService
from .models import (
    WorkerConfiguration,
    WorkerIterationStatus,
    WorkerStartupStatus,
)
from .sqlite_migrations import initialize_database
from .sqlite_repositories import (
    SqliteWorkerJournalRepository,
    SqliteWorkflowCommandRepository,
    _canonical_database_path,
)
from .worker import DevelopmentWorker, WorkerHandlerRegistry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="panam-development-worker")
    parser.add_argument("--database-path", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--poll-interval-seconds", type=float, default=1.0)
    parser.add_argument("--heartbeat-interval-seconds", type=float, default=5.0)
    parser.add_argument(
        "--heartbeat-stale-after-seconds", type=float, default=30.0
    )
    parser.add_argument("--lease-duration-seconds", type=int, default=60)
    parser.add_argument("--lease-renew-margin-seconds", type=float, default=5.0)
    parser.add_argument("--shutdown-grace-seconds", type=float, default=30.0)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def _configuration(namespace: argparse.Namespace) -> WorkerConfiguration:
    configuration = WorkerConfiguration(
        database_path=Path(namespace.database_path),
        worker_id=namespace.worker_id,
        poll_interval_seconds=namespace.poll_interval_seconds,
        heartbeat_interval_seconds=namespace.heartbeat_interval_seconds,
        heartbeat_stale_after_seconds=namespace.heartbeat_stale_after_seconds,
        lease_duration_seconds=namespace.lease_duration_seconds,
        lease_renew_margin_seconds=namespace.lease_renew_margin_seconds,
        shutdown_grace_seconds=namespace.shutdown_grace_seconds,
    )
    _canonical_database_path(configuration.database_path)
    return configuration


def _startup_exit(status: WorkerStartupStatus) -> int:
    if status in {
        WorkerStartupStatus.STARTED_NO_RECOVERY,
        WorkerStartupStatus.STARTED_AFTER_CLAIM_RECOVERY,
        WorkerStartupStatus.STARTED_AFTER_TERMINAL_MIRRORING,
        WorkerStartupStatus.STARTED_AFTER_MIXED_RECOVERY,
    }:
        return 0
    if status is WorkerStartupStatus.CONFIGURATION_INVALID:
        return 3
    if status in {
        WorkerStartupStatus.HANDLER_REGISTRY_EMPTY,
        WorkerStartupStatus.HANDLER_REGISTRY_INVALID,
        WorkerStartupStatus.HANDLER_REGISTRY_OVERSIZED,
    }:
        return 4
    if status is WorkerStartupStatus.ACTIVE_SESSION_EXISTS:
        return 5
    if status in {
        WorkerStartupStatus.STALE_SESSION_RECONCILIATION_REQUIRED,
        WorkerStartupStatus.CLAIM_LEASE_ACTIVE_RECONCILIATION_REQUIRED,
        WorkerStartupStatus.AMBIGUOUS_RUNNING,
        WorkerStartupStatus.CANCELLATION_OWNER_RECONCILIATION_REQUIRED,
    }:
        return 6
    return 7


def main(
    argv: Sequence[str] | None = None,
    *,
    registry_provider: Callable[[], WorkerHandlerRegistry] | None = None,
) -> int:
    namespace = _parser().parse_args(argv)
    try:
        configuration = _configuration(namespace)
    except (TypeError, ValueError, OSError):
        return 3
    if namespace.validate_only:
        return 0
    if registry_provider is None:
        return 4
    try:
        registry = registry_provider()
    except Exception:
        return 4
    if type(registry) is not WorkerHandlerRegistry or not registry._frozen:
        return 4
    try:
        keys = registry.keys()
    except (RuntimeError, TypeError, ValueError):
        return 4
    if not keys or len(keys) > 256 or registry._definition_registry is None:
        return 4

    stop_event = threading.Event()
    clock = lambda: datetime.now(timezone.utc)
    id_factory = lambda: str(uuid.uuid4())
    queue_repository = SqliteWorkflowCommandRepository(configuration.database_path)
    queue_service = DurableCommandQueueService(
        queue_repository,
        registry._definition_registry,
        configuration.lease_duration_seconds,
        clock=clock,
        id_factory=id_factory,
    )
    journal = SqliteWorkerJournalRepository(configuration.database_path)
    worker = DevelopmentWorker(
        configuration,
        queue_service,
        journal,
        registry,
        initialize_database,
        clock=clock,
        monotonic_clock=time.monotonic,
        wait=stop_event.wait,
        stop_requested=stop_event.is_set,
        session_id_factory=id_factory,
        operation_id_factory=id_factory,
        queue_database_path=configuration.database_path,
        journal_database_path=configuration.database_path,
    )
    startup = worker.start()
    startup_exit = _startup_exit(startup.status)
    if startup_exit:
        return startup_exit
    previous_sigint: object | None = None
    signal_installed = False
    if threading.current_thread() is threading.main_thread():
        previous_sigint = signal.getsignal(signal.SIGINT)

        def request_cooperative_stop(
            _signum: int, _frame: object | None
        ) -> None:
            stop_event.set()
            worker._request_cooperative_stop()

        signal.signal(signal.SIGINT, request_cooperative_stop)
        signal_installed = True
    try:
        result = worker.run_forever()
    except BaseException:
        # SIGINT itself is bridged to the Event above.  A BaseException that
        # reaches this boundary therefore came from execution (including a
        # handler-originated KeyboardInterrupt) and is never a clean stop.
        return 7
    finally:
        if signal_installed:
            signal.signal(signal.SIGINT, previous_sigint)  # type: ignore[arg-type]
    if result.status is WorkerIterationStatus.RECONCILIATION_REQUIRED:
        return 6
    if result.status is WorkerIterationStatus.STOPPED:
        if worker._stop_bridge_failure:
            return 7
        if result.detail_code is not None:
            return (
                6
                if result.detail_code
                in {
                    "SESSION_FENCED",
                    "LEASE_LOST",
                    "RECONCILIATION_REQUIRED",
                    "SHUTDOWN_GRACE_EXPIRED",
                }
                else 7
            )
        shutdown = worker.shutdown()
        return (
            0
            if shutdown.session is not None
            and shutdown.session.state.value == "STOPPED"
            else 7
        )
    return 7


if __name__ == "__main__":
    raise SystemExit(main())
