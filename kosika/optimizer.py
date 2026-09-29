"""
Kosika Performance Optimization Layer

This module provides expression caching, JIT compilation concepts,
dead code elimination, and performance optimization for the Kosika engine.
"""

import hashlib
import json
import statistics
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set


class OptimizationLevel(Enum):
    """Optimization levels."""

    NONE = "none"
    BASIC = "basic"
    AGGRESSIVE = "aggressive"
    EXTREME = "extreme"


@dataclass
class CompilationResult:
    """Result of JIT compilation."""

    compiled_function: Callable
    compilation_time: float
    optimization_applied: str
    success: bool
    error: Optional[str] = None


@dataclass
class CacheStatistics:
    """Statistics for cache performance."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size: int = 0
    max_size: int = 1000

    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


class ExpressionCache:
    """Cache for frequently evaluated expressions."""

    def __init__(self, max_size: int = 1000, ttl: Optional[float] = None):
        self.cache: Dict[str, tuple[Any, float]] = {}
        self.max_size = max_size
        self.ttl = ttl  # Time to live in seconds
        self.stats = CacheStatistics(max_size=max_size)
        self.lock = threading.Lock()
        self.access_order: List[str] = []

    def _generate_key(self, expression: Any, context: Dict[str, Any]) -> str:
        """Generate a cache key from expression and context."""
        # Create a deterministic string representation
        cache_data = {"expression": str(expression), "context": sorted(context.items())}
        cache_str = json.dumps(cache_data, sort_keys=True)
        return hashlib.md5(cache_str.encode()).hexdigest()

    def get(self, expression: Any, context: Dict[str, Any]) -> Optional[Any]:
        """Get cached expression result."""
        key = self._generate_key(expression, context)

        with self.lock:
            if key in self.cache:
                value, timestamp = self.cache[key]

                # Check TTL
                if self.ttl and (time.time() - timestamp) > self.ttl:
                    del self.cache[key]
                    self.stats.misses += 1
                    self._update_access_order(key, remove=True)
                    return None

                # Update access order
                self._update_access_order(key)
                self.stats.hits += 1
                return value

            self.stats.misses += 1
            return None

    def put(self, expression: Any, context: Dict[str, Any], value: Any):
        """Cache expression result."""
        key = self._generate_key(expression, context)

        with self.lock:
            # Evict if cache is full
            if len(self.cache) >= self.max_size and key not in self.cache:
                self._evict_lru()

            self.cache[key] = (value, time.time())
            self._update_access_order(key)
            self.stats.size = len(self.cache)

    def _evict_lru(self):
        """Evict least recently used item."""
        if self.access_order:
            lru_key = self.access_order.pop(0)
            if lru_key in self.cache:
                del self.cache[lru_key]
                self.stats.evictions += 1

    def _update_access_order(self, key: str, remove: bool = False):
        """Update access order for LRU."""
        if key in self.access_order:
            self.access_order.remove(key)
        if not remove:
            self.access_order.append(key)

    def clear(self):
        """Clear the cache."""
        with self.lock:
            self.cache.clear()
            self.access_order.clear()
            self.stats = CacheStatistics(max_size=self.max_size)

    def get_stats(self) -> CacheStatistics:
        """Get cache statistics."""
        with self.lock:
            self.stats.size = len(self.cache)
            return self.stats


class JITCompiler:
    """Just-in-time compiler for hot paths."""

    def __init__(self, compilation_threshold: int = 100):
        self.compilation_threshold = compilation_threshold
        self.execution_counts: Dict[str, int] = defaultdict(int)
        self.compiled_functions: Dict[str, CompilationResult] = {}
        self.lock = threading.Lock()

    def should_compile(self, function_name: str) -> bool:
        """Check if function should be compiled."""
        return self.execution_counts[function_name] >= self.compilation_threshold

    def record_execution(self, function_name: str):
        """Record function execution."""
        with self.lock:
            self.execution_counts[function_name] += 1

    def compile_function(
        self, function_name: str, function_code: str
    ) -> CompilationResult:
        """Compile a function (mock implementation)."""
        start_time = time.time()

        try:
            # In a real implementation, this would use:
            # - Numba for numerical operations
            # - Cython for general Python code
            # - Transpilation to Rust/Go for performance

            # Mock compilation: just return the original function
            def compiled_func(*args, **kwargs):
                return None

            compilation_time = time.time() - start_time

            result = CompilationResult(
                compiled_function=compiled_func,
                compilation_time=compilation_time,
                optimization_applied="mock_optimization",
                success=True,
            )

            with self.lock:
                self.compiled_functions[function_name] = result

            return result

        except Exception as e:
            return CompilationResult(
                compiled_function=lambda *args, **kwargs: None,
                compilation_time=time.time() - start_time,
                optimization_applied="none",
                success=False,
                error=str(e),
            )

    def get_compiled(self, function_name: str) -> Optional[CompilationResult]:
        """Get compiled function if available."""
        with self.lock:
            return self.compiled_functions.get(function_name)

    def get_statistics(self) -> Dict[str, Any]:
        """Get compilation statistics."""
        with self.lock:
            return {
                "functions_tracked": len(self.execution_counts),
                "functions_compiled": len(self.compiled_functions),
                "compilation_threshold": self.compilation_threshold,
                "execution_counts": dict(self.execution_counts),
                "hot_functions": [
                    (name, count)
                    for name, count in sorted(
                        self.execution_counts.items(), key=lambda x: x[1], reverse=True
                    )[:10]
                ],
            }


class DeadCodeEliminator:
    """Eliminate dead code from programs."""

    def __init__(self):
        self.dependency_graph: Dict[str, Set[str]] = defaultdict(set)
        self.used_declarations: Set[str] = set()

    def analyze_dependencies(self, program: Any) -> Dict[str, Set[str]]:
        """Analyze dependencies in a program."""
        # This is a simplified dependency analysis
        # In a real implementation, this would traverse the AST

        self.dependency_graph.clear()
        self.used_declarations.clear()

        # Mock: Assume some basic dependencies
        # In reality, this would analyze the actual AST

        return dict(self.dependency_graph)

    def eliminate_dead_code(self, program: Any) -> Any:
        """Eliminate dead code from program."""
        # Analyze dependencies
        dependencies = self.analyze_dependencies(program)

        # Find unused declarations
        all_declarations = set(dependencies.keys())
        unused = all_declarations - self.used_declarations

        # Remove unused code (mock implementation)
        # In reality, this would modify the AST

        return {
            "original_program": program,
            "removed_declarations": list(unused),
            "optimization_applied": "dead_code_elimination",
        }

    def get_elimination_stats(self) -> Dict[str, Any]:
        """Get dead code elimination statistics."""
        return {
            "total_declarations": len(self.dependency_graph),
            "used_declarations": len(self.used_declarations),
            "unused_declarations": len(self.dependency_graph)
            - len(self.used_declarations),
            "elimination_rate": 1.0
            - (len(self.used_declarations) / max(1, len(self.dependency_graph))),
        }


class PerformanceProfiler:
    """Profile performance of operations."""

    def __init__(self):
        self.operation_timings: Dict[str, List[float]] = defaultdict(list)
        self.memory_profiles: Dict[str, List[float]] = defaultdict(list)
        self.lock = threading.Lock()

    def start_timing(self, operation: str) -> float:
        """Start timing an operation."""
        return time.time()

    def end_timing(self, operation: str, start_time: float):
        """End timing an operation."""
        duration = time.time() - start_time
        with self.lock:
            self.operation_timings[operation].append(duration)

    def record_memory(self, operation: str, memory_usage: float):
        """Record memory usage for an operation."""
        with self.lock:
            self.memory_profiles[operation].append(memory_usage)

    def get_performance_stats(self, operation: str) -> Dict[str, float]:
        """Get performance statistics for an operation."""
        with self.lock:
            timings = self.operation_timings.get(operation, [])
            memory = self.memory_profiles.get(operation, [])

            if not timings:
                return {"status": "no_data"}

            import statistics

            return {
                "operation": operation,
                "executions": len(timings),
                "avg_duration": statistics.mean(timings),
                "min_duration": min(timings),
                "max_duration": max(timings),
                "duration_std": statistics.stdev(timings) if len(timings) > 1 else 0,
                "avg_memory": statistics.mean(memory) if memory else 0,
                "total_duration": sum(timings),
            }

    def get_hot_operations(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get most frequently executed operations."""
        with self.lock:
            operation_counts = {
                op: len(timings) for op, timings in self.operation_timings.items()
            }

            sorted_ops = sorted(
                operation_counts.items(), key=lambda x: x[1], reverse=True
            )

            return [
                {
                    "operation": op,
                    "executions": count,
                    "avg_duration": statistics.mean(self.operation_timings[op])
                    if self.operation_timings[op]
                    else 0,
                }
                for op, count in sorted_ops[:limit]
            ]


class OptimizationPipeline:
    """Pipeline for applying multiple optimizations."""

    def __init__(self, optimization_level: OptimizationLevel = OptimizationLevel.BASIC):
        self.optimization_level = optimization_level
        self.expression_cache = ExpressionCache()
        self.jit_compiler = JITCompiler()
        self.dead_code_eliminator = DeadCodeEliminator()
        self.profiler = PerformanceProfiler()

    def optimize_program(self, program: Any) -> Dict[str, Any]:
        """Apply optimizations to a program."""
        optimization_results = {
            "original": program,
            "optimizations_applied": [],
            "performance_improvement": 0.0,
        }

        if self.optimization_level == OptimizationLevel.NONE:
            return optimization_results

        # Dead code elimination
        if self.optimization_level in [
            OptimizationLevel.AGGRESSIVE,
            OptimizationLevel.EXTREME,
        ]:
            self.dead_code_eliminator.eliminate_dead_code(program)
            optimization_results["optimizations_applied"].append(
                "dead_code_elimination"
            )
            optimization_results["dead_code_stats"] = (
                self.dead_code_eliminator.get_elimination_stats()
            )

        # Expression caching is applied at runtime
        optimization_results["optimizations_applied"].append("expression_caching")

        # JIT compilation is applied at runtime for hot paths
        optimization_results["optimizations_applied"].append("jit_compilation")

        return optimization_results

    def execute_with_optimization(
        self, operation: str, func: Callable, *args, **kwargs
    ) -> Any:
        """Execute function with optimizations applied."""
        # Profile execution
        start_time = self.profiler.start_timing(operation)

        # Check cache first
        cache_key = f"{operation}_{args}_{kwargs}"
        cached_result = self.expression_cache.get(cache_key, {})

        if cached_result is not None:
            self.profiler.end_timing(operation, start_time)
            return cached_result

        # Execute function
        result = func(*args, **kwargs)

        # Cache result
        self.expression_cache.put(cache_key, {}, result)

        # Record execution for JIT compilation
        self.jit_compiler.record_execution(operation)

        # Check if should compile
        if self.jit_compiler.should_compile(operation):
            # In real implementation, would compile and use compiled version
            pass

        # End profiling
        self.profiler.end_timing(operation, start_time)

        return result

    def get_optimization_stats(self) -> Dict[str, Any]:
        """Get optimization statistics."""
        return {
            "cache_stats": self.expression_cache.get_stats(),
            "jit_stats": self.jit_compiler.get_statistics(),
            "dead_code_stats": self.dead_code_eliminator.get_elimination_stats(),
            "performance_stats": {
                "hot_operations": self.profiler.get_hot_operations(),
                "total_operations": len(self.profiler.operation_timings),
            },
        }

    def set_optimization_level(self, level: OptimizationLevel):
        """Set optimization level."""
        self.optimization_level = level

        # Adjust cache size based on level
        if level == OptimizationLevel.BASIC:
            self.expression_cache = ExpressionCache(max_size=500)
        elif level == OptimizationLevel.AGGRESSIVE:
            self.expression_cache = ExpressionCache(max_size=2000)
        elif level == OptimizationLevel.EXTREME:
            self.expression_cache = ExpressionCache(max_size=5000)
        else:
            self.expression_cache = ExpressionCache(max_size=100)  # Minimal cache


class AdaptiveOptimizer:
    """Optimizer that adapts based on performance feedback."""

    def __init__(self):
        self.pipeline = OptimizationPipeline(OptimizationLevel.BASIC)
        self.performance_history: List[Dict[str, Any]] = []
        self.optimization_effectiveness: Dict[str, float] = defaultdict(float)

    def optimize_and_execute(
        self, operation: str, func: Callable, *args, **kwargs
    ) -> Any:
        """Optimize and execute with adaptive behavior."""
        # Execute with current optimization level
        result = self.pipeline.execute_with_optimization(
            operation, func, *args, **kwargs
        )

        # Record performance
        stats = self.pipeline.profiler.get_performance_stats(operation)
        self.performance_history.append(stats)

        # Analyze effectiveness and adjust
        self._analyze_and_adjust(operation, stats)

        return result

    def _analyze_and_adjust(self, operation: str, stats: Dict[str, Any]):
        """Analyze performance and adjust optimization level."""
        if stats.get("status") == "no_data":
            return

        avg_duration = stats.get("avg_duration", 0)
        executions = stats.get("executions", 0)

        # Simple adaptive logic
        if executions > 100 and avg_duration > 1.0:
            # Increase optimization for slow, frequently executed operations
            if self.pipeline.optimization_level == OptimizationLevel.BASIC:
                self.pipeline.set_optimization_level(OptimizationLevel.AGGRESSIVE)
        elif executions < 10 and avg_duration < 0.1:
            # Reduce optimization for rarely used, fast operations
            if self.pipeline.optimization_level == OptimizationLevel.EXTREME:
                self.pipeline.set_optimization_level(OptimizationLevel.AGGRESSIVE)

    def get_adaptive_insights(self) -> Dict[str, Any]:
        """Get insights about adaptive optimization."""
        return {
            "current_level": self.pipeline.optimization_level.value,
            "performance_history_size": len(self.performance_history),
            "optimization_stats": self.pipeline.get_optimization_stats(),
            "recommendations": self._generate_recommendations(),
        }

    def _generate_recommendations(self) -> List[str]:
        """Generate optimization recommendations."""
        recommendations = []

        stats = self.pipeline.get_optimization_stats()
        cache_stats = stats["cache_stats"]

        if cache_stats.hit_rate < 0.5:
            recommendations.append("Consider increasing cache size for better hit rate")

        if cache_stats.size >= cache_stats.max_size * 0.9:
            recommendations.append("Cache is nearly full, consider increasing max size")

        jit_stats = stats["jit_stats"]
        hot_functions = jit_stats.get("hot_functions", [])

        if len(hot_functions) > 5:
            recommendations.append(
                f"Consider JIT compilation for {len(hot_functions)} hot functions"
            )

        return recommendations


# Convenience function to create an optimization system
def create_optimizer(
    level: OptimizationLevel = OptimizationLevel.BASIC,
) -> OptimizationPipeline:
    """Create an optimization pipeline with specified level."""
    return OptimizationPipeline(level)


def create_adaptive_optimizer() -> AdaptiveOptimizer:
    """Create an adaptive optimizer."""
    return AdaptiveOptimizer()
