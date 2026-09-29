"""
Kosika Backend Infrastructure

This module provides robust backend infrastructure for Kosika including
monitoring, distributed execution, caching, and observability systems.
"""

import time
import threading
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Callable
from enum import Enum
import json
import hashlib


class MetricType(Enum):
    """Types of metrics."""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


@dataclass
class Metric:
    """A single metric measurement."""
    name: str
    value: float
    metric_type: MetricType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class Span:
    """A span for distributed tracing."""
    span_id: str
    parent_id: Optional[str]
    operation_name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    tags: Dict[str, str] = field(default_factory=dict)
    logs: List[Dict[str, Any]] = field(default_factory=list)

    def duration(self) -> Optional[float]:
        """Calculate span duration in seconds."""
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None


class MetricsCollector:
    """Collects and aggregates metrics."""

    def __init__(self):
        self.metrics: Dict[str, List[Metric]] = defaultdict(list)
        self.counters: Dict[str, float] = defaultdict(float)
        self.gauges: Dict[str, float] = defaultdict(float)
        self.histograms: Dict[str, List[float]] = defaultdict(list)
        self.lock = threading.Lock()

    def increment(self, name: str, value: float = 1.0, tags: Optional[Dict[str, str]] = None):
        """Increment a counter metric."""
        with self.lock:
            self.counters[name] += value
            metric = Metric(
                name=name,
                value=self.counters[name],
                metric_type=MetricType.COUNTER,
                tags=tags or {}
            )
            self.metrics[name].append(metric)

    def set_gauge(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Set a gauge metric."""
        with self.lock:
            self.gauges[name] = value
            metric = Metric(
                name=name,
                value=value,
                metric_type=MetricType.GAUGE,
                tags=tags or {}
            )
            self.metrics[name].append(metric)

    def observe(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Observe a value for a histogram."""
        with self.lock:
            self.histograms[name].append(value)
            metric = Metric(
                name=name,
                value=value,
                metric_type=MetricType.HISTOGRAM,
                tags=tags or {}
            )
            self.metrics[name].append(metric)

    def get_metric(self, name: str) -> Optional[List[Metric]]:
        """Get all metrics for a name."""
        with self.lock:
            return self.metrics.get(name)

    def get_all_metrics(self) -> Dict[str, List[Metric]]:
        """Get all collected metrics."""
        with self.lock:
            return dict(self.metrics)

    def reset(self):
        """Reset all metrics."""
        with self.lock:
            self.metrics.clear()
            self.counters.clear()
            self.gauges.clear()
            self.histograms.clear()


class DistributedTracer:
    """Distributed tracing for execution flows."""

    def __init__(self):
        self.spans: Dict[str, Span] = {}
        self.root_spans: List[str] = []
        self.lock = threading.Lock()

    def start_span(self, operation_name: str, parent_id: Optional[str] = None) -> Span:
        """Start a new span."""
        span_id = str(uuid.uuid4())
        span = Span(
            span_id=span_id,
            parent_id=parent_id,
            operation_name=operation_name,
            start_time=datetime.now(timezone.utc)
        )

        with self.lock:
            self.spans[span_id] = span
            if parent_id is None:
                self.root_spans.append(span_id)

        return span

    def finish_span(self, span_id: str, tags: Optional[Dict[str, str]] = None):
        """Finish a span."""
        with self.lock:
            if span_id in self.spans:
                span = self.spans[span_id]
                span.end_time = datetime.now(timezone.utc)
                if tags:
                    span.tags.update(tags)

    def add_log(self, span_id: str, log_entry: Dict[str, Any]):
        """Add a log entry to a span."""
        with self.lock:
            if span_id in self.spans:
                self.spans[span_id].logs.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    **log_entry
                })

    def get_span(self, span_id: str) -> Optional[Span]:
        """Get a span by ID."""
        with self.lock:
            return self.spans.get(span_id)

    def get_trace(self, span_id: str) -> List[Span]:
        """Get all spans in a trace."""
        trace = []
        current_span = self.get_span(span_id)

        while current_span:
            trace.append(current_span)
            if current_span.parent_id:
                current_span = self.get_span(current_span.parent_id)
            else:
                break

        return trace


class ResourceMonitor:
    """Monitor system resources."""

    def __init__(self):
        self.metrics_collector = MetricsCollector()
        self.monitoring_thread = None
        self.stop_event = threading.Event()
        self.monitor_interval = 1.0  # seconds

    def start_monitoring(self):
        """Start resource monitoring."""
        if self.monitoring_thread is None:
            self.stop_event.clear()
            self.monitoring_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.monitoring_thread.start()

    def stop_monitoring(self):
        """Stop resource monitoring."""
        if self.monitoring_thread:
            self.stop_event.set()
            self.monitoring_thread.join()
            self.monitoring_thread = None

    def _monitor_loop(self):
        """Main monitoring loop."""
        try:
            import psutil
            import os
            psutil_available = True
        except ImportError:
            psutil_available = False
            print("psutil not available, resource monitoring limited")

        while not self.stop_event.is_set():
            try:
                if psutil_available:
                    # CPU usage
                    cpu_percent = psutil.cpu_percent()
                    self.metrics_collector.set_gauge("system.cpu.percent", cpu_percent)

                    # Memory usage
                    memory = psutil.virtual_memory()
                    self.metrics_collector.set_gauge("system.memory.percent", memory.percent)
                    self.metrics_collector.set_gauge("system.memory.available", memory.available)

                    # Process-specific metrics
                    process = psutil.Process(os.getpid())
                    self.metrics_collector.set_gauge("process.memory.percent", process.memory_percent())
                    self.metrics_collector.set_gauge("process.cpu.percent", process.cpu_percent())

                # Thread count (always available)
                thread_count = threading.active_count()
                self.metrics_collector.set_gauge("system.threads.count", thread_count)

            except Exception as e:
                print(f"Monitoring error: {e}")

            self.stop_event.wait(self.monitor_interval)

    def get_current_metrics(self) -> Dict[str, float]:
        """Get current resource metrics."""
        return {
            "cpu_percent": self.metrics_collector.gauges.get("system.cpu.percent", 0.0),
            "memory_percent": self.metrics_collector.gauges.get("system.memory.percent", 0.0),
            "memory_available": self.metrics_collector.gauges.get("system.memory.available", 0.0),
            "process_memory_percent": self.metrics_collector.gauges.get("process.memory.percent", 0.0),
            "process_cpu_percent": self.metrics_collector.gauges.get("process.cpu.percent", 0.0),
            "thread_count": self.metrics_collector.gauges.get("system.threads.count", 0.0)
        }


class CacheEntry:
    """A cache entry with metadata."""

    def __init__(self, key: str, value: Any, ttl: Optional[float] = None):
        self.key = key
        self.value = value
        self.created_at = datetime.now(timezone.utc)
        self.ttl = ttl
        self.access_count = 0
        self.last_accessed = self.created_at

    def is_expired(self) -> bool:
        """Check if the entry is expired."""
        if self.ttl is None:
            return False
        return (datetime.now(timezone.utc) - self.created_at).total_seconds() > self.ttl

    def touch(self):
        """Update access information."""
        self.access_count += 1
        self.last_accessed = datetime.now(timezone.utc)


class LRUCache:
    """Least Recently Used cache implementation."""

    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self.cache: Dict[str, CacheEntry] = {}
        self.access_order: deque = deque()
        self.lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Get a value from the cache."""
        with self.lock:
            if key in self.cache:
                entry = self.cache[key]
                if entry.is_expired():
                    del self.cache[key]
                    self.misses += 1
                    return None

                entry.touch()
                self.hits += 1
                return entry.value

            self.misses += 1
            return None

    def put(self, key: str, value: Any, ttl: Optional[float] = None):
        """Put a value in the cache."""
        with self.lock:
            if key in self.cache:
                del self.cache[key]

            if len(self.cache) >= self.max_size:
                # Evict least recently used
                oldest_key = self.access_order.popleft()
                if oldest_key in self.cache:
                    del self.cache[oldest_key]

            entry = CacheEntry(key, value, ttl)
            self.cache[key] = entry
            self.access_order.append(key)

    def remove(self, key: str):
        """Remove a key from the cache."""
        with self.lock:
            if key in self.cache:
                del self.cache[key]
                try:
                    self.access_order.remove(key)
                except ValueError:
                    pass

    def clear(self):
        """Clear the cache."""
        with self.lock:
            self.cache.clear()
            self.access_order.clear()
            self.hits = 0
            self.misses = 0

    def size(self) -> int:
        """Get the current cache size."""
        with self.lock:
            return len(self.cache)

    def hit_rate(self) -> float:
        """Get the cache hit rate."""
        with self.lock:
            total = self.hits + self.misses
            if total == 0:
                return 0.0
            return self.hits / total

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self.lock:
            return {
                "size": len(self.cache),
                "max_size": self.max_size,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": self.hit_rate()
            }


class MultiLevelCache:
    """Multi-level cache with different backends."""

    def __init__(self):
        self.l1_cache = LRUCache(max_size=100)  # In-memory L1
        self.l2_cache = LRUCache(max_size=1000)  # In-memory L2
        self.metrics = MetricsCollector()

    def get(self, key: str) -> Optional[Any]:
        """Get value from multi-level cache."""
        # Try L1 first
        value = self.l1_cache.get(key)
        if value is not None:
            self.metrics.increment("cache.l1.hits")
            return value

        self.metrics.increment("cache.l1.misses")

        # Try L2
        value = self.l2_cache.get(key)
        if value is not None:
            self.metrics.increment("cache.l2.hits")
            # Promote to L1
            self.l1_cache.put(key, value)
            return value

        self.metrics.increment("cache.l2.misses")
        return None

    def put(self, key: str, value: Any, ttl: Optional[float] = None):
        """Put value in multi-level cache."""
        self.l1_cache.put(key, value, ttl)
        self.l2_cache.put(key, value, ttl)

    def clear(self):
        """Clear all cache levels."""
        self.l1_cache.clear()
        self.l2_cache.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get combined cache statistics."""
        return {
            "l1": self.l1_cache.get_stats(),
            "l2": self.l2_cache.get_stats(),
            "overall": {
                "l1_hit_rate": self.l1_cache.hit_rate(),
                "l2_hit_rate": self.l2_cache.hit_rate()
            }
        }


class AlertingSystem:
    """Alerting system for monitoring anomalies."""

    def __init__(self):
        self.alert_rules: List[Callable[[Dict[str, float]], Optional[str]]] = []
        self.alert_history: List[Dict[str, Any]] = []
        self.lock = threading.Lock()

    def add_alert_rule(self, rule: Callable[[Dict[str, float]], Optional[str]]):
        """Add an alert rule."""
        self.alert_rules.append(rule)

    def check_alerts(self, metrics: Dict[str, float]) -> List[str]:
        """Check all alert rules against current metrics."""
        alerts = []
        for rule in self.alert_rules:
            alert_message = rule(metrics)
            if alert_message:
                alerts.append(alert_message)
                self._record_alert(alert_message, metrics)

        return alerts

    def _record_alert(self, message: str, metrics: Dict[str, float]):
        """Record an alert in history."""
        with self.lock:
            self.alert_history.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": message,
                "metrics": metrics.copy()
            })

    def get_alert_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent alert history."""
        with self.lock:
            return self.alert_history[-limit:]


class MonitoringSystem:
    """Comprehensive monitoring system."""

    def __init__(self):
        self.metrics_collector = MetricsCollector()
        self.tracer = DistributedTracer()
        self.resource_monitor = ResourceMonitor()
        self.cache_stats = MultiLevelCache()
        self.alerting = AlertingSystem()
        self.structured_logger = StructuredLogger()

        # Setup default alert rules
        self._setup_default_alerts()

    def _setup_default_alerts(self):
        """Setup default alerting rules."""
        def high_cpu_alert(metrics: Dict[str, float]) -> Optional[str]:
            if metrics.get("cpu_percent", 0) > 90:
                return f"High CPU usage: {metrics['cpu_percent']}%"
            return None

        def high_memory_alert(metrics: Dict[str, float]) -> Optional[str]:
            if metrics.get("memory_percent", 0) > 90:
                return f"High memory usage: {metrics['memory_percent']}%"
            return None

        self.alerting.add_alert_rule(high_cpu_alert)
        self.alerting.add_alert_rule(high_memory_alert)

    def start(self):
        """Start monitoring system."""
        self.resource_monitor.start_monitoring()

    def stop(self):
        """Stop monitoring system."""
        self.resource_monitor.stop_monitoring()

    def collect_metrics(self) -> Dict[str, Any]:
        """Collect all system metrics."""
        resource_metrics = self.resource_monitor.get_current_metrics()
        cache_metrics = self.cache_stats.get_stats()

        combined = {
            "resources": resource_metrics,
            "cache": cache_metrics,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # Check for alerts
        alerts = self.alerting.check_alerts(resource_metrics)
        if alerts:
            combined["alerts"] = alerts
            self.structured_logger.warning("Alerts triggered", alerts=alerts)

        return combined

    def trace_execution(self, operation_name: str, parent_id: Optional[str] = None) -> Span:
        """Start tracing an execution."""
        return self.tracer.start_span(operation_name, parent_id)


class StructuredLogger:
    """Structured logging system."""

    def __init__(self):
        self.logs: List[Dict[str, Any]] = []
        self.lock = threading.Lock()

    def log(self, level: str, message: str, **kwargs):
        """Log a structured message."""
        with self.lock:
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "message": message,
                **kwargs
            }
            self.logs.append(log_entry)

    def info(self, message: str, **kwargs):
        """Log an info message."""
        self.log("INFO", message, **kwargs)

    def warning(self, message: str, **kwargs):
        """Log a warning message."""
        self.log("WARNING", message, **kwargs)

    def error(self, message: str, **kwargs):
        """Log an error message."""
        self.log("ERROR", message, **kwargs)

    def debug(self, message: str, **kwargs):
        """Log a debug message."""
        self.log("DEBUG", message, **kwargs)

    def get_logs(self, level: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get logs, optionally filtered by level."""
        with self.lock:
            if level:
                filtered = [log for log in self.logs if log["level"] == level]
                return filtered[-limit:]
            return self.logs[-limit:]


# Convenience function to create a complete monitoring setup
def create_monitoring_system() -> MonitoringSystem:
    """Create a complete monitoring system with default configuration."""
    system = MonitoringSystem()
    system.start()
    return system