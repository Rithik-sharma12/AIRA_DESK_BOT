from laptop_server.llm.base import GenerationStats
from laptop_server.monitoring.metrics import MetricsRecorder


def test_summary_excludes_cold_loads_from_latency():
    m = MetricsRecorder()
    m.record(GenerationStats(model="m", load_s=8.0, ttft_s=9.0, completion_tokens=10, eval_s=1.0))
    for t in (0.4, 0.5, 0.6):
        m.record(GenerationStats(model="m", ttft_s=t, completion_tokens=40, eval_s=2.0))
    s = m.summary()
    assert s["requests"] == 4 and s["warm_requests"] == 3
    assert s["ttft_s"]["p50"] == 0.5
    assert s["tokens_per_s"]["mean"] == 20.0
    assert s["max_load_s"] == 8.0
