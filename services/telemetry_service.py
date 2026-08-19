"""
Telemetry and Observability Service for MockupGen.

Tracks in-memory real-time metrics, request lifecycles, error diagnostics with stack traces,
server resource utilization (CPU, RAM, threads, disk), and mockup rendering performance.
"""

from __future__ import annotations

import os
import sys
import time
import shutil
import threading
import traceback
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False


@dataclass
class RequestRecord:
    id: str
    timestamp: str
    method: str
    path: str
    status: int
    duration_ms: float
    client_ip: str
    request_size: int
    response_size: int
    error: str | None = None
    is_slow: bool = False


@dataclass
class ErrorRecord:
    id: str
    timestamp: str
    method: str
    path: str
    status: int
    error_type: str
    error_message: str
    traceback: str
    client_ip: str


@dataclass
class RenderMetric:
    timestamp: str
    template_id: str
    mode: str
    duration_ms: float
    success: bool
    error: str | None = None


class TelemetryService:
    def __init__(
        self,
        upload_folder: Path | str,
        output_folder: Path | str,
        templates_folder: Path | str,
        max_request_history: int = 500,
        max_error_history: int = 200,
    ) -> None:
        self.upload_folder = Path(upload_folder)
        self.output_folder = Path(output_folder)
        self.templates_folder = Path(templates_folder)
        self.max_request_history = max_request_history
        self.max_error_history = max_error_history

        self.start_time = time.time()
        self._lock = threading.Lock()

        # Ring buffers for history
        self.recent_requests: deque[RequestRecord] = deque(maxlen=max_request_history)
        self.recent_errors: deque[ErrorRecord] = deque(maxlen=max_error_history)
        self.render_metrics: deque[RenderMetric] = deque(maxlen=300)

        # Rolling counters
        self.total_requests = 0
        self.status_2xx = 0
        self.status_3xx = 0
        self.status_4xx = 0
        self.status_5xx = 0
        self.total_duration_ms = 0.0

        # Rolling minute timestamps for throughput (timestamps within last 60 seconds)
        self._recent_timestamps: deque[float] = deque(maxlen=2000)

    def record_request(
        self,
        request_id: str,
        method: str,
        path: str,
        status: int,
        duration_ms: float,
        client_ip: str,
        request_size: int = 0,
        response_size: int = 0,
        error: str | None = None,
    ) -> None:
        now_ts = time.time()
        record = RequestRecord(
            id=request_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            method=method,
            path=path,
            status=status,
            duration_ms=round(duration_ms, 2),
            client_ip=client_ip,
            request_size=request_size,
            response_size=response_size,
            error=error,
            is_slow=duration_ms >= 1000.0,
        )

        with self._lock:
            self.recent_requests.appendleft(record)
            self.total_requests += 1
            self.total_duration_ms += duration_ms
            self._recent_timestamps.append(now_ts)

            if 200 <= status < 300:
                self.status_2xx += 1
            elif 300 <= status < 400:
                self.status_3xx += 1
            elif 400 <= status < 500:
                self.status_4xx += 1
            elif status >= 500:
                self.status_5xx += 1

    def record_error(
        self,
        request_id: str,
        method: str,
        path: str,
        status: int,
        exc: Exception | None = None,
        error_message: str | None = None,
        client_ip: str = "127.0.0.1",
    ) -> None:
        tb_str = ""
        error_type = "Error"
        if exc is not None:
            error_type = type(exc).__name__
            tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            if not error_message:
                error_message = str(exc) or error_type
        elif not error_message:
            error_message = f"HTTP {status}"

        error_rec = ErrorRecord(
            id=request_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            method=method,
            path=path,
            status=status,
            error_type=error_type,
            error_message=error_message or "Unknown error",
            traceback=tb_str,
            client_ip=client_ip,
        )

        with self._lock:
            self.recent_errors.appendleft(error_rec)

    def record_render(
        self,
        template_id: str,
        mode: str,
        duration_ms: float,
        success: bool,
        error: str | None = None,
    ) -> None:
        metric = RenderMetric(
            timestamp=datetime.now(timezone.utc).isoformat(),
            template_id=template_id,
            mode=mode,
            duration_ms=round(duration_ms, 2),
            success=success,
            error=error,
        )
        with self._lock:
            self.render_metrics.appendleft(metric)

    def get_system_metrics(self) -> dict[str, Any]:
        uptime_seconds = int(time.time() - self.start_time)
        cpu_percent = 0.0
        process_ram_mb = 0.0
        system_ram_percent = 0.0
        system_ram_total_gb = 0.0
        threads_count = threading.active_count()

        if _PSUTIL_AVAILABLE:
            try:
                proc = psutil.Process()
                cpu_percent = proc.cpu_percent(interval=None)
                process_ram_mb = round(proc.memory_info().rss / (1024 * 1024), 1)
                sys_mem = psutil.virtual_memory()
                system_ram_percent = sys_mem.percent
                system_ram_total_gb = round(sys_mem.total / (1024**3), 1)
            except Exception:
                pass

        return {
            "uptime_seconds": uptime_seconds,
            "uptime_formatted": self._format_uptime(uptime_seconds),
            "cpu_percent": cpu_percent,
            "process_ram_mb": process_ram_mb,
            "system_ram_percent": system_ram_percent,
            "system_ram_total_gb": system_ram_total_gb,
            "active_threads": threads_count,
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "platform": sys.platform,
        }

    def get_storage_metrics(self) -> dict[str, Any]:
        def folder_size(path: Path) -> int:
            if not path.is_dir():
                return 0
            total = 0
            try:
                for entry in path.rglob("*"):
                    if entry.is_file():
                        total += entry.stat().st_size
            except Exception:
                pass
            return total

        uploads_bytes = folder_size(self.upload_folder)
        outputs_bytes = folder_size(self.output_folder)
        templates_bytes = folder_size(self.templates_folder)

        return {
            "uploads_mb": round(uploads_bytes / (1024 * 1024), 2),
            "outputs_mb": round(outputs_bytes / (1024 * 1024), 2),
            "templates_mb": round(templates_bytes / (1024 * 1024), 2),
            "total_storage_mb": round((uploads_bytes + outputs_bytes + templates_bytes) / (1024 * 1024), 2),
        }

    def get_summary(self) -> dict[str, Any]:
        now_ts = time.time()
        with self._lock:
            # Calculate requests in last 60 seconds
            cutoff_1m = now_ts - 60.0
            req_last_1m = sum(1 for t in self._recent_timestamps if t >= cutoff_1m)

            total_req = self.total_requests
            status_2xx = self.status_2xx
            status_3xx = self.status_3xx
            status_4xx = self.status_4xx
            status_5xx = self.status_5xx
            total_dur = self.total_duration_ms

            avg_latency = round(total_dur / total_req, 2) if total_req > 0 else 0.0
            total_errors = status_4xx + status_5xx
            success_rate = (
                round(((total_req - total_errors) / total_req) * 100, 1)
                if total_req > 0
                else 100.0
            )

            # Analyze top templates rendered
            template_counts: dict[str, int] = {}
            render_durations: list[float] = []
            for m in self.render_metrics:
                template_counts[m.template_id] = template_counts.get(m.template_id, 0) + 1
                if m.success:
                    render_durations.append(m.duration_ms)

            avg_render_ms = (
                round(sum(render_durations) / len(render_durations), 1)
                if render_durations
                else 0.0
            )
            top_templates = [
                {"template_id": tid, "count": cnt}
                for tid, cnt in sorted(template_counts.items(), key=lambda item: item[1], reverse=True)[:6]
            ]

            # Recent error count
            error_count = len(self.recent_errors)

        system = self.get_system_metrics()
        storage = self.get_storage_metrics()

        return {
            "system": system,
            "storage": storage,
            "requests": {
                "total": total_req,
                "per_minute": req_last_1m,
                "status_2xx": status_2xx,
                "status_3xx": status_3xx,
                "status_4xx": status_4xx,
                "status_5xx": status_5xx,
                "success_rate": success_rate,
                "avg_latency_ms": avg_latency,
            },
            "rendering": {
                "total_renders": len(self.render_metrics),
                "avg_render_ms": avg_render_ms,
                "top_templates": top_templates,
            },
            "errors_logged": error_count,
        }

    def get_recent_requests(
        self, limit: int = 50, status_filter: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self.recent_requests)

        filtered = []
        for r in items:
            if status_filter == "2xx" and not (200 <= r.status < 300):
                continue
            if status_filter == "4xx" and not (400 <= r.status < 500):
                continue
            if status_filter == "5xx" and not (r.status >= 500):
                continue
            if status_filter == "slow" and not r.is_slow:
                continue
            if status_filter == "errors" and r.status < 400:
                continue
            filtered.append(asdict(r))
            if len(filtered) >= limit:
                break

        return filtered

    def get_recent_errors(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self.recent_errors)
        return [asdict(e) for e in items[:limit]]

    def purge_temp_files(self, max_age_hours: float = 24.0) -> dict[str, Any]:
        """Deletes files in outputs older than max_age_hours."""
        now = time.time()
        max_age_seconds = max_age_hours * 3600
        deleted_count = 0
        freed_bytes = 0

        for folder in (self.output_folder, self.upload_folder):
            if not folder.is_dir():
                continue
            try:
                for path in folder.glob("*"):
                    if path.is_file():
                        try:
                            mtime = path.stat().st_mtime
                            if (now - mtime) > max_age_seconds:
                                size = path.stat().st_size
                                path.unlink(missing_ok=True)
                                deleted_count += 1
                                freed_bytes += size
                        except Exception:
                            pass
            except Exception:
                pass

        return {
            "deleted_count": deleted_count,
            "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        }

    def clear_logs(self) -> None:
        with self._lock:
            self.recent_requests.clear()
            self.recent_errors.clear()
            self.render_metrics.clear()
            self.total_requests = 0
            self.status_2xx = 0
            self.status_3xx = 0
            self.status_4xx = 0
            self.status_5xx = 0
            self.total_duration_ms = 0.0
            self._recent_timestamps.clear()

    @staticmethod
    def _format_uptime(seconds: int) -> str:
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, secs = divmod(rem, 60)
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        if minutes > 0:
            return f"{minutes}m {secs}s"
        return f"{secs}s"
