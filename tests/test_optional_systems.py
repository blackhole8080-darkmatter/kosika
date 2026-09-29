from datetime import datetime, timezone

import pytest

from kosika.cloud import (
    CloudExecutor,
    ExecutionRequest,
    ExecutionResult,
    ProgramLoader,
    ProgramManifest,
    ProgramPackage,
    ProgramRegistry,
    create_package,
    find_program,
    register_package,
)
from kosika.intelligence import (
    AdaptiveExecutor,
    ErrorPredictor,
    ErrorSeverity,
    ExecutionProfile,
    ExecutionStrategy,
    LearningSystem,
    PatternExtractor,
    ResourcePredictor,
    create_intelligence_system,
)
from kosika.meta_agent import (
    AgentCapability,
    MetaAgent,
    ModelCapability,
    TaskContext,
    TaskType,
    ToolCapability,
    select_optimal_configuration,
)
from kosika.optimizer import (
    AdaptiveOptimizer,
    DeadCodeEliminator,
    ExpressionCache,
    JITCompiler,
    OptimizationLevel,
    OptimizationPipeline,
    PerformanceProfiler,
    create_adaptive_optimizer,
    create_optimizer,
)


def make_package(name="demo", version="1.0.0"):
    manifest = ProgramManifest(
        name=name,
        version=version,
        author="tester",
        capabilities=["analysis"],
        requires=[],
        main_file="main.kos",
        modules=["main.kos"],
        hash="",
        description="A test package",
        tags=["demo"],
    )
    package = ProgramPackage(manifest, {"main.kos": "workflow main() {}\n"})
    manifest.hash = package.compute_hash()
    return package


def test_cloud_package_loader_registry_and_requests(tmp_path):
    source_dir = tmp_path / "sample"
    source_dir.mkdir()
    (source_dir / "main.kos").write_text("workflow main() {}\n")
    (source_dir / "helpers.kos").write_text("record Note { text: Text }\n")

    package = ProgramLoader.from_directory(source_dir)
    assert (
        package.manifest.main_file == "helpers.kos"
        or package.manifest.main_file == "main.kos"
    )
    assert set(package.source_files) == {"main.kos", "helpers.kos"}
    assert len(package.manifest.hash) == 64
    assert create_package(source_dir).source_files == package.source_files

    restored = ProgramPackage.from_dict(package.to_dict())
    assert restored.manifest.to_dict() == package.manifest.to_dict()
    assert restored.source_files == package.source_files

    registry = ProgramRegistry(tmp_path / "registry")
    program_id = register_package(package, registry)
    assert program_id == "local/sample"
    assert registry.get(program_id).source_files == package.source_files
    assert registry.get(program_id, "missing") is None
    assert registry.get("missing") is None
    assert registry.list() == [
        {
            "id": "local/sample",
            "name": "sample",
            "author": "local",
            "versions": ["1.0.0"],
            "latest": "1.0.0",
        }
    ]

    named = make_package()
    assert registry.register(named) == "tester/demo"
    assert registry.list("tester")[0]["id"] == "tester/demo"
    assert registry.list("missing") == []
    assert registry.search("demo")[0]["id"] == "tester/demo"
    assert registry.search("analysis")[0]["capabilities"] == ["analysis"]
    assert registry.search("absent") == []
    assert find_program("analysis", registry)[0]["id"] == "tester/demo"

    request = ExecutionRequest(
        "tester/demo", input_data={"text": "hi"}, options={"dry_run": True}
    )
    assert request.to_dict() == {
        "program_id": "tester/demo",
        "version": None,
        "input": {"text": "hi"},
        "options": {"dry_run": True},
    }
    result = ExecutionResult("run-1", "completed", {"ok": True}, trace="trace.jsonl")
    assert result.to_dict()["status"] == "completed"
    assert result.to_dict()["trace"] == "trace.jsonl"

    with pytest.raises(FileNotFoundError):
        ProgramLoader.from_directory(tmp_path / "missing")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="No .kos"):
        ProgramLoader.from_directory(empty)
    with pytest.raises(NotImplementedError):
        ProgramLoader.from_url("https://example.test/program")
    with pytest.raises(NotImplementedError):
        ProgramLoader.from_github("owner/repo")
    executor = CloudExecutor("https://example.test", "secret")
    with pytest.raises(NotImplementedError):
        executor.execute(request)
    with pytest.raises(NotImplementedError):
        executor.get_status("run-1")


def test_expression_cache_jit_and_profiler_cover_real_transitions():
    cache = ExpressionCache(max_size=1)
    assert cache.get("x", {}) is None
    cache.put("x", {}, 10)
    assert cache.get("x", {}) == 10
    cache.put("y", {}, 20)
    assert cache.get("x", {}) is None
    assert cache.get_stats().evictions == 1
    cache.clear()
    assert cache.get_stats().size == 0

    expired = ExpressionCache(ttl=-1)
    expired.put("x", {}, 10)
    assert expired.get("x", {}) is None
    assert expired.get_stats().misses == 1

    jit = JITCompiler(compilation_threshold=2)
    assert not jit.should_compile("hot")
    jit.record_execution("hot")
    jit.record_execution("hot")
    assert jit.should_compile("hot")
    compiled = jit.compile_function("hot", "return 1")
    assert compiled.success
    assert compiled.compiled_function() is None
    assert jit.get_compiled("hot") is compiled
    assert jit.get_statistics()["hot_functions"] == [("hot", 2)]

    eliminator = DeadCodeEliminator()
    assert eliminator.eliminate_dead_code("program")["original_program"] == "program"
    assert eliminator.get_elimination_stats()["elimination_rate"] == 1.0

    profiler = PerformanceProfiler()
    assert profiler.get_performance_stats("missing") == {"status": "no_data"}
    start = profiler.start_timing("read")
    profiler.end_timing("read", start)
    profiler.record_memory("read", 4)
    stats = profiler.get_performance_stats("read")
    assert stats["executions"] == 1
    assert stats["avg_memory"] == 4
    assert profiler.get_hot_operations()[0]["operation"] == "read"


def test_optimization_pipeline_levels_and_adaptive_execution():
    none = OptimizationPipeline(OptimizationLevel.NONE)
    assert none.optimize_program("program")["optimizations_applied"] == []

    pipeline = create_optimizer()
    assert pipeline.optimize_program("program")["optimizations_applied"] == [
        "expression_caching",
        "jit_compilation",
    ]
    assert pipeline.execute_with_optimization("add", lambda value: value + 1, 1) == 2
    assert pipeline.execute_with_optimization("add", lambda value: value + 1, 1) == 2
    stats = pipeline.get_optimization_stats()
    assert stats["cache_stats"].hits == 1
    assert stats["jit_stats"]["functions_tracked"] == 1

    pipeline.set_optimization_level(OptimizationLevel.AGGRESSIVE)
    aggressive = pipeline.optimize_program("program")
    assert "dead_code_elimination" in aggressive["optimizations_applied"]
    pipeline.set_optimization_level(OptimizationLevel.EXTREME)
    assert pipeline.expression_cache.max_size == 5000
    pipeline.set_optimization_level(OptimizationLevel.NONE)
    assert pipeline.expression_cache.max_size == 100

    adaptive = create_adaptive_optimizer()
    assert adaptive.optimize_and_execute("noop", lambda: "ok") == "ok"
    insights = adaptive.get_adaptive_insights()
    assert insights["performance_history_size"] == 1
    assert isinstance(adaptive, AdaptiveOptimizer)


def test_meta_agent_selects_capabilities_and_respects_constraints():
    meta = MetaAgent()
    assert meta.analyze_task("analyze this PDF") == TaskType.DOCUMENT_ANALYSIS
    assert meta.analyze_task("write a function") == TaskType.CODE_GENERATION
    assert meta.analyze_task("crawl the web") == TaskType.WEB_SCRAPING
    assert meta.analyze_task("inspect this image") == TaskType.IMAGE_ANALYSIS
    assert meta.analyze_task("answer this question") == TaskType.QUESTION_ANSWERING
    assert meta.analyze_task("create a report") == TaskType.TEXT_GENERATION
    assert meta.analyze_task("reason about the result") == TaskType.REASONING
    assert meta.analyze_task("unknown task") == TaskType.TEXT_GENERATION

    context = TaskContext(
        "analyze a document",
        TaskType.DOCUMENT_ANALYSIS,
        input_size=100,
        urgency="high",
        quality_requirement="high",
        privacy_level="public",
        budget_limit=1.0,
    )
    assert meta.select_agent(context) == ["document-analyzer"]
    assert meta.select_tools(context, ["file-reader", "missing"]) == ["file-reader"]
    assert meta.select_model(context, ["missing", "gpt-4"]) == "gpt-4"
    cost, latency = meta.estimate_execution(
        "document-analyzer", "gpt-4", ["file-reader"], context
    )
    assert cost > 0
    assert latency == 7.1

    local = ModelCapability(
        "local-doc",
        "local",
        [TaskType.DOCUMENT_ANALYSIS],
        0.0,
        4096,
        1.0,
        0.5,
        False,
        False,
    )
    meta.register_model(local)
    confidential = TaskContext(
        "document",
        TaskType.DOCUMENT_ANALYSIS,
        10,
        "low",
        "low",
        "confidential",
    )
    assert meta.select_model(confidential, ["local-doc", "gpt-4"]) == "local-doc"

    meta.register_tool(ToolCapability("custom", "custom", [], [], 0.2, 0.3, 0.9))
    meta.register_agent(
        AgentCapability(
            "custom-agent",
            [TaskType.DOCUMENT_ANALYSIS],
            ["custom"],
            ["gpt-4"],
            0.1,
            1.0,
            0.8,
            1000,
            0.9,
        )
    )
    result = meta.select("analyze a document", context)
    assert result.selected_agent == "document-analyzer"
    assert result.selected_tools == ["file-reader"]
    assert result.configuration["task_type"] == "document_analysis"
    assert result.confidence_score > 0
    assert result.reasoning.startswith("Selected document-analyzer")
    assert result.alternatives[0]["agent"] == "custom-agent"

    with pytest.raises(ValueError, match="No model"):
        meta.select_model(
            TaskContext("x", TaskType.MULTIMODAL, 1, "low", "low", "public"), []
        )
    with pytest.raises(ValueError, match="Agent or model"):
        meta.estimate_execution("missing", "missing", [], context)
    too_large = TaskContext(
        "document", TaskType.DOCUMENT_ANALYSIS, 99_999_999, "low", "low", "public"
    )
    with pytest.raises(ValueError, match="No suitable agent"):
        meta.select("analyze a document", too_large)
    assert (
        select_optimal_configuration("answer a question").selected_agent
        == "general-assistant"
    )


def _profile(operation, index, success=True, duration=5.0, memory=20.0):
    return ExecutionProfile(
        execution_id=f"run-{index}",
        operation=operation,
        duration=duration,
        resource_usage={"cpu": 0.5, "memory_percent": memory},
        success=success,
        timestamp=datetime(2024, 1, index + 1, tzinfo=timezone.utc),
    )


def test_intelligence_patterns_predictions_resources_and_learning():
    extractor = PatternExtractor()
    for index in range(5):
        extractor.add_execution(
            _profile("alpha", index, success=index < 3, duration=index + 1)
        )
    for index in range(5):
        extractor.add_execution(_profile("beta", index, duration=index + 1.1))
    alpha = extractor.get_pattern("alpha")
    assert alpha is not None
    assert alpha.frequency == 5
    assert alpha.success_rate == 0.6
    assert extractor.get_similar_patterns("alpha", threshold=0.8)
    assert extractor.get_pattern("missing") is None
    error_predictor = ErrorPredictor()
    predictions = error_predictor.analyze_code("recursive while true large_file", {})
    assert {prediction.error_type for prediction in predictions} == {
        "stack_overflow",
        "infinite_loop",
        "memory_exhaustion",
    }
    pattern_predictions = error_predictor.predict_from_pattern(alpha)
    assert any(
        p.error_type == "timeout"
        for p in error_predictor.predict_from_pattern(
            alpha.__class__(
                alpha.pattern_id,
                alpha.operation_type,
                31,
                alpha.avg_resource_usage,
                0.5,
                5,
                alpha.last_seen,
            )
        )
    )
    assert any(p.error_type == "unreliable_operation" for p in pattern_predictions)
    context_predictions = error_predictor.predict_from_context(
        {
            "memory_limit": 1,
            "estimated_memory": 2,
            "time_limit": 1,
            "estimated_duration": 2,
        }
    )
    assert {prediction.error_type for prediction in context_predictions} == {
        "memory_exhaustion",
        "timeout",
    }
    assert ErrorSeverity.HIGH.value == "high"

    resources = ResourcePredictor()
    baseline = resources.predict_resources("model_call", {"input_size": 2000})
    assert baseline.confidence == 0.3
    assert baseline.cpu_estimate == 1.6
    for index in range(3):
        resources.add_resource_data(
            "custom", {"cpu": index + 1, "memory": 10, "duration": 2}
        )
    historical = resources.predict_resources("custom", {"input_size": 1000})
    assert historical.confidence == 0.8
    assert historical.cpu_estimate == 2
    assert resources.predict_resources("file_reader", {}).memory_estimate == 20

    adaptive = AdaptiveExecutor()
    analysis = adaptive.analyze_operation(
        "analyze_task", {"input_size": 2000, "parallel_available": True}
    )
    assert analysis["recommended_strategy"] == ExecutionStrategy.PARALLEL
    assert analysis["resource_prediction"].confidence == 0.3
    for index in range(5):
        adaptive.record_execution(_profile("complex_operation", index))
    assert adaptive.get_performance_insights("missing")["status"] == "insufficient_data"
    insights = adaptive.get_performance_insights("complex_operation")
    assert insights["executions"] == 5
    assert insights["trend"] == "stable"
    assert (
        adaptive._recommend_strategy(
            "x",
            {"distributed_available": True},
            {
                "resource_prediction": resources.predict_resources(
                    "complex", {"input_size": 10000}
                )
            },
        )
        == ExecutionStrategy.DISTRIBUTED
    )
    assert (
        adaptive._recommend_strategy(
            "x",
            {"has_dependencies": True, "parallel_available": True},
            {
                "resource_prediction": resources.predict_resources(
                    "simple", {"input_size": 1}
                )
            },
        )
        == ExecutionStrategy.HYBRID
    )

    learning = LearningSystem()
    assert learning.train_simple_model("x")["status"] == "insufficient_data"
    for index in range(1, 11):
        learning.add_training_data({"x": index}, index * 2)
    model = learning.train_simple_model("x")
    assert model["slope"] == 2
    assert learning.predict({"x": 3})["x"] == 6
    assert learning.get_feature_importance()["x"] == 2


def test_intelligence_system_caches_and_records_results():
    system = create_intelligence_system()
    first = system.analyze_and_optimize("simple_operation", {"input_size": 10})
    second = system.analyze_and_optimize("simple_operation", {"input_size": 10})
    assert first is second
    system.record_execution_result(
        "simple_operation", 1.0, True, {"cpu": 0.2, "memory": 10}
    )
    system.record_execution_result(
        "simple_operation", 2.0, False, {"cpu": 0.3, "memory": 12}
    )
    insights = system.get_system_insights()
    assert insights["training_samples"] == 2
    assert insights["top_operations"] == [{"operation": "simple_operation", "count": 2}]
    assert (
        system.adaptive_executor.get_performance_insights("simple_operation")[
            "executions"
        ]
        == 2
    )
