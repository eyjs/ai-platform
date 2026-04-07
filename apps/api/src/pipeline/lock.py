"""파이프라인 파일 레벨 락.

병렬 Implementor의 동시 접근을 제어한다.
os.open() + O_CREAT | O_EXCL 원자적 파일 생성으로 race condition을 방지한다.

외부 의존성 없이 표준 라이브러리만 사용한다.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

STALE_TIMEOUT_MINUTES = 5


def acquire_task_lock(
    task_id: str,
    agent_id: str,
    pipeline_dir: str = ".pipeline",
) -> bool:
    """태스크 파일 락 획득.

    O_CREAT | O_EXCL 플래그로 원자적 파일 생성을 시도한다.
    이미 락 파일이 존재하면 False를 반환한다.

    Args:
        task_id: 태스크 ID (예: "001")
        agent_id: 에이전트 식별자
        pipeline_dir: 파이프라인 디렉토리 경로

    Returns:
        True: 락 획득 성공
        False: 이미 다른 에이전트가 락 보유
    """
    lock_path = _lock_file_path(task_id, pipeline_dir)
    lock_data = {
        "locked_by": agent_id,
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "pid": os.getpid(),
    }

    try:
        # O_CREAT | O_EXCL: 파일이 이미 존재하면 FileExistsError
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, json.dumps(lock_data, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(fd)
        logger.info(
            "task_lock_acquired",
            extra={"task_id": task_id, "agent_id": agent_id},
        )
        return True
    except FileExistsError:
        logger.info(
            "task_lock_already_held",
            extra={"task_id": task_id, "agent_id": agent_id},
        )
        return False


def release_task_lock(
    task_id: str,
    pipeline_dir: str = ".pipeline",
) -> None:
    """태스크 파일 락 해제.

    락 파일이 없으면 경고 로그 후 무시한다.
    """
    lock_path = _lock_file_path(task_id, pipeline_dir)
    try:
        os.remove(str(lock_path))
        logger.info("task_lock_released", extra={"task_id": task_id})
    except FileNotFoundError:
        logger.warning(
            "task_lock_not_found_on_release",
            extra={"task_id": task_id},
        )


def is_stale_lock(
    task_id: str,
    pipeline_dir: str = ".pipeline",
    timeout_minutes: int = STALE_TIMEOUT_MINUTES,
) -> bool:
    """stale 락 판정.

    locked_at 기준 timeout_minutes 초과 시 True.
    락 파일이 없거나 파싱 실패 시 False.
    """
    lock_path = _lock_file_path(task_id, pipeline_dir)
    try:
        with open(str(lock_path), "r", encoding="utf-8") as f:
            lock_data = json.load(f)
        locked_at = datetime.fromisoformat(lock_data["locked_at"])
        elapsed = datetime.now(timezone.utc) - locked_at
        return elapsed.total_seconds() > timeout_minutes * 60
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        return False


def cleanup_stale_locks(
    pipeline_dir: str = ".pipeline",
    timeout_minutes: int = STALE_TIMEOUT_MINUTES,
) -> int:
    """stale 락 일괄 정리.

    pipeline_dir/tasks/ 디렉토리의 모든 .lock 파일을 순회하여
    stale 판정된 락을 삭제한다.

    Returns:
        삭제된 stale 락 파일 수
    """
    tasks_dir = Path(pipeline_dir) / "tasks"
    if not tasks_dir.exists():
        return 0

    cleaned = 0
    for lock_file in tasks_dir.glob("*.lock"):
        # 파일명에서 task_id 추출: task-001.lock -> 001
        stem = lock_file.stem
        if stem.startswith("task-"):
            task_id = stem[len("task-"):]
        else:
            continue

        if is_stale_lock(task_id, pipeline_dir, timeout_minutes):
            try:
                os.remove(str(lock_file))
                logger.info(
                    "stale_lock_cleaned",
                    extra={"task_id": task_id, "lock_file": str(lock_file)},
                )
                cleaned += 1
            except FileNotFoundError:
                pass

    if cleaned > 0:
        logger.info("stale_locks_cleanup_complete", extra={"cleaned": cleaned})
    return cleaned


def atomic_write_json(filepath: str, data: dict) -> None:
    """JSON 원자적 쓰기.

    임시 파일에 작성 후 os.replace()로 원자적 교체한다.
    status.json 업데이트 시 사용하여 중간 상태 노출을 방지한다.
    """
    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
    except Exception:
        # 임시 파일 정리 시도
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass
        raise


def _lock_file_path(task_id: str, pipeline_dir: str) -> Path:
    """락 파일 경로 생성."""
    return Path(pipeline_dir) / "tasks" / f"task-{task_id}.lock"
