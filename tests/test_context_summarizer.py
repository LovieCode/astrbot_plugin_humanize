"""Behavior tests for the LLM context-summary digest and its window plumbing."""

from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot_plugin_humanize.humanize.context.summarizer import ContextSummarizer
from tests.test_group_context import _context, _window


class _FakeBridge:
    def __init__(self, *, reply: str = "", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.prompts: list[str] = []

    async def complete(self, prompt: str, *, system_prompt: str = "") -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.reply


def test_sanitize_keeps_bracket_lines_and_whitelisted_refs() -> None:
    summarizer = ContextSummarizer(_FakeBridge(), max_chars=1_000)
    raw = "\n".join(
        [
            "[小红 · 07-19 08:01] 约好周六爬山（ctx-2A2B3C4D）",
            "好的，这条不是摘要行，应被丢弃",
            "[小明 · 07-19 08:02] 买了票（ctx-FAKEFAKE）",
            "",
        ]
    )
    result = summarizer.sanitize(raw, {"ctx-2A2B3C4D"})
    assert result is not None
    assert result.splitlines() == [
        "[小红 · 07-19 08:01] 约好周六爬山（ctx-2A2B3C4D）",
        "[小明 · 07-19 08:02] 买了票",
    ]


def test_sanitize_drops_oldest_lines_when_over_budget() -> None:
    # 构造函数把字符上限钳到 200 下限；5 行 × 66 字必然超限触发裁剪。
    summarizer = ContextSummarizer(_FakeBridge(), max_chars=200)
    raw = "\n".join(f"[成员{i} · 08:00] {'很长的消息' * 10}" for i in range(1, 6))
    result = summarizer.sanitize(raw, set())
    assert result is not None
    assert len(result) <= 200
    # 超预算时丢最旧的行，保留最新内容。
    assert "[成员5 · " in result
    assert "[成员1 · " not in result


def test_sanitize_rejects_everything_without_bracket_lines() -> None:
    summarizer = ContextSummarizer(_FakeBridge())
    assert summarizer.sanitize("随便说点啥\n没有任何前缀", set()) is None


def test_digest_happy_path_and_provider_failure() -> None:
    bridge = _FakeBridge(reply="[小红 · 07-19 08:01] 摘要（ctx-2A2B3C4D）")
    summarizer = ContextSummarizer(bridge, max_chars=500)
    source = "[小红 · 07-19 08:01] 很长的原文（ctx-2A2B3C4D）"

    async def scenario() -> None:
        digest = await summarizer.digest(source)
        assert digest == "[小红 · 07-19 08:01] 摘要（ctx-2A2B3C4D）"
        # prompt 带上原文与字符预算。
        assert source in bridge.prompts[0]
        assert "500" in bridge.prompts[0]

        failed = ContextSummarizer(
            _FakeBridge(error=RuntimeError("provider down")), max_chars=500
        )
        assert await failed.digest(source) is None
        # 空输入直接跳过。
        assert await summarizer.digest("   ") is None

    asyncio.run(scenario())


def test_window_refresh_summary_replaces_text_and_rearms_on_compaction(
    tmp_path: Path,
) -> None:
    """压缩→LLM 摘要落盘→重复刷新 no-op→新压缩复位标记→CAS 失配放弃。"""

    class _FakeSummarizer:
        def __init__(self) -> None:
            self.seen: list[str] = []
            self.reply = "[小红 · 07-19 08:05] 合并后的摘要（ctx-2A2B3C4D）"

        async def digest(self, text: str) -> str:
            self.seen.append(text)
            return self.reply

    async def scenario() -> None:
        window, _, _ = await _window(tmp_path)
        for index in range(1, 31):
            await window.append_chatter(
                _context(index, sender_id=f"user-{index}", sender_name="小红"),
                token_budget=256,
            )
        # 未挂摘要器时刷新是 no-op。
        assert await window.refresh_summary(_context(99)) is False

        summarizer = _FakeSummarizer()
        window.attach_summarizer(summarizer)  # type: ignore[arg-type]
        assert await window.refresh_summary(_context(99)) is True
        assert len(summarizer.seen) == 1
        assert "小红" in summarizer.seen[0]

        loaded = await window.load(_context(99), token_budget=30_000)
        summary = str(loaded.contexts[0].get("content"))
        assert "合并后的摘要" in summary
        # 已消化（llm=True）时重复刷新是 no-op。
        assert await window.refresh_summary(_context(99)) is False
        assert len(summarizer.seen) == 1

        # 新一轮压缩追加确定性行 → 只对新行做摘要，已摘要旧文本冻结保留
        # （避免「摘要的摘要」逐轮丢细节）。
        for index in range(31, 61):
            await window.append_chatter(
                _context(index, sender_id=f"user-{index}", sender_name="小红"),
                token_budget=256,
            )
        summarizer.reply = "[小红 · 07-19 08:10] 第二次摘要"
        assert await window.refresh_summary(_context(99)) is True
        loaded = await window.load(_context(99), token_budget=30_000)
        summary = str(loaded.contexts[0].get("content"))
        assert "第二次摘要" in summary
        assert "合并后的摘要" in summary  # 冻结部分不重压缩
        assert "小红" in summarizer.seen[1]  # digest 输入是新增行

        # CAS 失配：expected 不匹配时不写。
        applied = await asyncio.to_thread(
            window._apply_summary_sync,
            _context(99),
            "stale-text",
            "",
            "[x] 新文本",
        )
        assert applied is False

    asyncio.run(scenario())


def test_container_attaches_summarizer_only_with_provider(
    tmp_path: Path, monkeypatch
) -> None:
    """配置了提取 Provider 才挂摘要器；否则保持确定性摘要。"""
    from astrbot_plugin_humanize.humanize.config import PluginConfig
    from astrbot_plugin_humanize.humanize.container import Container

    monkeypatch.setattr(PluginConfig, "data_path", lambda self: tmp_path)

    with_provider = Container.build(
        PluginConfig(memory_extraction_provider_id="chat-provider"), None
    )
    assert with_provider.context_window._summarizer is not None

    without_provider = Container.build(PluginConfig(), None)
    assert without_provider.context_window._summarizer is None

    disabled = Container.build(
        PluginConfig(
            memory_extraction_provider_id="chat-provider",
            context_summary_enabled=False,
        ),
        None,
    )
    assert disabled.context_window._summarizer is None


def test_window_pending_stays_aligned_with_pruned_summary(
    tmp_path: Path,
) -> None:
    """预算裁剪丢最旧行时，pending 同步对齐，已裁行不「复活」。

    无 provider 期间多次压缩会让确定性行累积到 pending；若裁剪不同步
    对齐，已裁掉的旧行仍留在 pending——下次 refresh 的 digest 输入会
    把它们带回来，且 pending 无限膨胀。
    """
    import json

    async def scenario() -> None:
        window, _, _ = await _window(tmp_path)

        def long_body(index: int) -> str:
            return f"消息{index}：" + "很长很长的正文内容。" * 30

        # 两批超长消息：两次压缩都在预算内反复裁掉最旧的行。
        for batch in range(2):
            for index in range(batch * 60, batch * 60 + 60):
                await window.append_chatter(
                    _context(
                        index,
                        sender_id=f"user-{index}",
                        sender_name="小红",
                        user_text=long_body(index),
                    ),
                    token_budget=256,
                )
        _, _, session_directory = window._session_info(_context(99))
        state_path = session_directory / "context_window.json"
        with window._workspace.transaction() as transaction:
            raw = json.loads(transaction.read_bytes(state_path).decode("utf-8"))
        summary = raw["summary"]
        pending = [str(item) for item in summary.get("pending") or []]
        text_lines = str(summary.get("text") or "").splitlines()
        assert pending  # 未挂摘要器：确实累积了待消化行
        # pending 严格是 text 尾部的行：被预算裁掉的旧行不会留在里面。
        assert all(line in text_lines for line in pending)
        assert len(pending) <= len(text_lines)

        # 挂上摘要器后：digest 输入只含仍可见的确定性行。
        class _FakeSummarizer:
            def __init__(self) -> None:
                self.seen: list[str] = []

            async def digest(self, text: str) -> str:
                self.seen.append(text)
                return "[小红 · 07-19 12:00] 已消化"

        summarizer = _FakeSummarizer()
        window.attach_summarizer(summarizer)  # type: ignore[arg-type]
        assert await window.refresh_summary(_context(99)) is True
        assert summarizer.seen[0].splitlines() == pending

    asyncio.run(scenario())
