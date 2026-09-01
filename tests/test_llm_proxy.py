"""LLM 调用代理层（全链路真实用量）的行为测试。

覆盖：调用上下文的嵌套作用域、Provider 回报 usage 的提取（含 raw usage
回退）、humanize_llm_call_log 落库、代理仅在插件标记的调用上下文内记录、
失败调用记录 error 并原样抛出、转述链路带 call_type 与请求关联，以及
usage-overview 对回复链样本与代理调用日志的合并聚合。
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pytest
from astrbot_plugin_humanize.humanize.image_cache import ImageCacheStore
from astrbot_plugin_humanize.humanize.llm_proxy import (
    current_llm_call_context,
    llm_call_context,
    llm_response_usage,
)
from astrbot_plugin_humanize.humanize.repositories.sqlite import (
    _SCHEMA_VERSION,
    SQLiteRepository,
)
from astrbot_plugin_humanize.main import HumanizePlugin


class _TranscribeProvider:
    """转述 Provider 桩：记录调用，返回带 Provider 真实 usage 的响应。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.contexts_seen: list[dict[str, str] | None] = []

    async def get_provider_by_id(self, provider_id: str) -> _TranscribeProvider:
        return self

    async def text_chat(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        self.contexts_seen.append(current_llm_call_context())
        return NS(
            completion_text="转述完成",
            usage=NS(input_cached=0, input_other=21, output=6),
            raw_completion=None,
        )


class _ProviderManagerStub:
    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def get_provider_by_id(self, provider_id: str) -> Any:
        return self._provider


def _identity_provider(provider_id: str = "prov-a", model: str = "vlm-1") -> NS:
    return NS(
        provider_config={"id": provider_id, "type": "openai", "model": model},
        meta=lambda: NS(id=provider_id, type="openai", model=model),
    )


def _plugin_with_repository(repository: SQLiteRepository) -> HumanizePlugin:
    plugin = HumanizePlugin(NS(), {"memory_enabled": False})
    plugin._container = NS(repository=repository)
    return plugin


def test_llm_call_context_scopes_and_restores() -> None:
    async def scenario() -> None:
        assert current_llm_call_context() is None
        async with llm_call_context("transcribe_sticker", request_id="req-1"):
            outer = current_llm_call_context()
            assert outer is not None
            assert outer["call_type"] == "transcribe_sticker"
            assert outer["request_id"] == "req-1"
            async with llm_call_context("extract", scope_type="group"):
                assert current_llm_call_context()["call_type"] == "extract"
            assert current_llm_call_context()["call_type"] == "transcribe_sticker"
        assert current_llm_call_context() is None

    asyncio.run(scenario())


def test_llm_response_usage_reads_provider_reported_tokens() -> None:
    usage, observed = llm_response_usage(
        NS(usage=NS(input_cached=11, input_other=7, output=3), raw_completion=None)
    )
    assert observed is True
    assert usage == {"input_cached": 11, "input_other": 7, "output": 3}

    # 适配器没填 normalized usage：以 raw usage 为准。
    usage, observed = llm_response_usage(
        NS(
            usage=None,
            raw_completion=NS(usage=NS(input_cached=2, input_other=4, output=1)),
        )
    )
    assert observed is True
    assert usage == {"input_cached": 2, "input_other": 4, "output": 1}

    # 空 normalized TokenUsage + raw 有值：取 raw，避免丢真实用量。
    usage, observed = llm_response_usage(
        NS(
            usage=NS(input_cached=0, input_other=0, output=0),
            raw_completion=NS(usage=NS(input_cached=9, input_other=8, output=5)),
        )
    )
    assert observed is True
    assert usage == {"input_cached": 9, "input_other": 8, "output": 5}

    # Provider 完全没有回报 usage：记 usage_observed=0、token 为 0，不估算。
    usage, observed = llm_response_usage(NS(usage=None, raw_completion=None))
    assert observed is False
    assert usage == {"input_cached": 0, "input_other": 0, "output": 0}


def test_usage_overview_merges_pipeline_and_proxied_calls(tmp_path: Path) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "humanize.db"
        repository = SQLiteRepository(db_path)
        await repository.initialize()

        await repository.record_llm_usage_sample(
            request_id="req-p1",
            stage="final",
            scope_type="group",
            scope_id="g1",
            provider_id="prov-a",
            provider_type="openai",
            model="deepseek-v4",
            input_cached=100,
            input_other=50,
            output_tokens=20,
            usage_observed=True,
            duration_ms=2_000,
        )
        await repository.record_llm_call(
            call_type="transcribe_sticker",
            scope_type="group",
            scope_id="g1",
            request_id="req-t1",
            provider_id="prov-b",
            provider_type="openai",
            model="vlm-1",
            input_cached=0,
            input_other=30,
            output_tokens=4,
            usage_observed=True,
            duration_ms=900,
        )
        await repository.record_llm_call(
            call_type="extract",
            status="error",
            error="TimeoutError: upstream timeout",
        )

        overview = await repository.get_usage_overview(days=7)
        totals = overview["totals"]
        assert totals["calls"] == 3
        assert totals["usage_observed_calls"] == 2
        assert totals["input_cached"] == 100
        assert totals["input_other"] == 80
        assert totals["output_tokens"] == 24
        assert totals["avg_duration_ms"] is not None
        assert abs(totals["avg_duration_ms"] - 966.7) < 0.1
        assert totals["cache_share"] == round(100 * 100 / 180, 1)

        by_model = {row["model"]: row for row in overview["by_model"]}
        assert by_model["deepseek-v4"]["calls"] == 1
        assert by_model["vlm-1"]["input_other"] == 30

        by_type = {
            (row["call_type"], row["source"]): row for row in overview["by_call_type"]
        }
        assert by_type[("final", "pipeline")]["calls"] == 1
        assert by_type[("transcribe_sticker", "aux")]["calls"] == 1
        assert by_type[("extract", "aux")]["errors"] == 1

        assert len(overview["daily"]) == 7
        assert overview["daily"][-1]["calls"] == 3

        assert set(recent_type_names(overview)) == {
            "final",
            "transcribe_sticker",
            "extract",
        }
        error_rows = recent_error_rows(overview)
        assert len(error_rows) == 1
        assert error_rows[0]["call_type"] == "extract"
        assert "TimeoutError" in str(error_rows[0]["error"])

        with sqlite3.connect(db_path) as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(humanize_llm_call_log)"
                ).fetchall()
            }
        assert version == _SCHEMA_VERSION
        assert {
            "call_type",
            "scope_type",
            "scope_id",
            "conversation_id",
            "request_id",
            "provider_id",
            "provider_type",
            "model",
            "input_cached",
            "input_other",
            "output_tokens",
            "usage_observed",
            "duration_ms",
            "status",
            "error",
            "created_at",
        } <= columns

    asyncio.run(scenario())


def recent_type_names(overview: dict[str, Any]) -> set[str]:
    return {row["call_type"] for row in overview["recent"]}


def recent_error_rows(overview: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in overview["recent"] if row["status"] == "error"]


def test_proxied_provider_call_records_only_tagged_calls(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        plugin = _plugin_with_repository(repository)
        provider = _identity_provider("prov-a", "vlm-1")

        async def fake_orig(_provider, **_kwargs):
            return NS(
                completion_text="ok",
                usage=NS(input_cached=10, input_other=5, output=2),
                raw_completion=None,
            )

        untagged = await plugin._proxied_provider_call(
            provider, fake_orig, (), {"model": "m-1"}
        )
        assert untagged.completion_text == "ok"
        # 未经 llm_call_context 标记的调用（管线/其他插件）不落代理日志。
        untagged_overview = await repository.get_usage_overview()
        assert untagged_overview["totals"]["calls"] == 0

        async with llm_call_context(
            "transcribe_sticker",
            scope_type="group",
            scope_id="g-1",
            request_id="req-9",
        ):
            tagged = await plugin._proxied_provider_call(
                provider, fake_orig, (), {"model": None}
            )
        assert tagged.completion_text == "ok"
        await asyncio.sleep(0.05)  # 等待代理记录任务落库

        overview = await repository.get_usage_overview(days=7)
        assert overview["totals"]["calls"] == 1
        assert overview["totals"]["input_cached"] == 10
        assert overview["totals"]["input_other"] == 5
        assert overview["totals"]["output_tokens"] == 2
        aux_rows = [row for row in overview["by_call_type"] if row["source"] == "aux"]
        assert [row["call_type"] for row in aux_rows] == ["transcribe_sticker"]
        assert overview["by_model"][0]["model"] == "vlm-1"
        assert overview["recent"][0]["request_id"] == "req-9"

    asyncio.run(scenario())


def test_proxied_provider_call_records_failure_and_reraises(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        plugin = _plugin_with_repository(repository)
        provider = _identity_provider("prov-b", "m-2")

        async def failing_orig(_provider, **_kwargs):
            raise TimeoutError("upstream timeout")

        with pytest.raises(TimeoutError):
            async with llm_call_context("openviking"):
                await plugin._proxied_provider_call(provider, failing_orig, (), {})
        await asyncio.sleep(0.05)  # 等待代理记录任务落库

        overview = await repository.get_usage_overview(days=7)
        assert overview["totals"]["calls"] == 1
        assert overview["totals"]["output_tokens"] == 0
        row = overview["by_call_type"][0]
        assert row["call_type"] == "openviking"
        assert row["errors"] == 1
        recent = overview["recent"][0]
        assert recent["status"] == "error"
        assert "TimeoutError" in str(recent["error"])
        assert recent["model"] == "m-2"

    asyncio.run(scenario())


def test_transcription_records_proxied_call_with_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """转述调用带 call_type=transcribe_* 与 scope/request 关联进入代理上下文。

    落库由补丁后的 Provider 方法（_proxied_provider_call）完成，已在
    ``test_proxied_provider_call_records_only_tagged_calls`` 覆盖；这里验证
    转述链路把 call_type 与 trace 关联正确传给代理上下文。
    """

    async def scenario() -> None:
        from astrbot.core.utils import astrbot_path

        monkeypatch.setattr(
            astrbot_path, "get_astrbot_plugin_data_path", lambda: str(tmp_path)
        )
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        plugin = HumanizePlugin(NS(), {"memory_enabled": False})
        plugin._plugin_config = replace(
            plugin._plugin_config, image_transcription_provider_id="prov-1"
        )
        plugin._image_store = ImageCacheStore(plugin._plugin_config, repository)
        plugin._container = NS(repository=repository)
        stub = _TranscribeProvider()
        plugin.context.provider_manager = _ProviderManagerStub(stub)

        text = await plugin._transcribe_one_image(
            str(tmp_path / "cache.png"),
            "看这个",
            kind="sticker",
            trace={"request_id": "req-t1", "scope_type": "group", "scope_id": "g-1"},
        )

        assert text == "转述完成"
        assert stub.contexts_seen[0] == {
            "call_type": "transcribe_sticker",
            "request_id": "req-t1",
            "scope_type": "group",
            "scope_id": "g-1",
            "conversation_id": "",
        }

    asyncio.run(scenario())


def test_call_log_schema_version_is_at_least_30() -> None:
    assert _SCHEMA_VERSION >= 30
