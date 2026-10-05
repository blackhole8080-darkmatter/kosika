from datetime import datetime, timezone

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
