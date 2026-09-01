"""表情包/普通图分类与逐张转述缓存的行为测试。

QQ 图片段的 ``sub_type``/``summary`` 是分类信号：非 0 或带 ``[xx]`` summary
的是表情包。表情包转述按内容 hash 长期缓存（命中不调模型、未命中回写），
普通图每次现转；引用消息里的图片经索引反查同样能吃到表情包缓存。转述注入
必须是纯文本——历史实现把 ImageCache dataclass 的 repr 拼进了消息标注。
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from astrbot_plugin_humanize.humanize.image_cache import ImageCacheStore
from astrbot_plugin_humanize.main import (
    _EVENT_IMAGE_CACHE_PATHS_KEY,
    _EVENT_IMAGE_TRANSCRIPTIONS_KEY,
    _IMAGE_TRANSCRIPTION_PROMPT,
    _STICKER_TRANSCRIPTION_PROMPT,
    HumanizePlugin,
    _direct_image_kinds,
)

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image, Reply
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata

_PLATFORM_META = PlatformMetadata(name="aiocqhttp", description="", id="aiocqhttp")

_STICKER_SEGMENT = {
    "type": "image",
    "data": {"summary": "[动画表情]", "file": "a.image", "sub_type": 1},
}
_PHOTO_SEGMENT = {
    "type": "image",
    "data": {"summary": "", "file": "b.image", "sub_type": 0},
}


def _kind_event(raw_message: Any) -> Any:
    return SimpleNamespace(message_obj=SimpleNamespace(raw_message=raw_message))


def test_direct_image_kinds_classifies_by_sub_type_and_summary() -> None:
    """text 段不占位；直发图按段顺序得到 (kind, summary) 分类。"""
    raw = {
        "message": [
            {"type": "text", "data": {"text": "看图"}},
            dict(_STICKER_SEGMENT),
            dict(_PHOTO_SEGMENT),
        ]
    }
    assert _direct_image_kinds(_kind_event(raw)) == [
        ("sticker", "[动画表情]"),
        ("image", ""),
    ]


def test_direct_image_kinds_edge_cases() -> None:
    raw = {
        "message": [
            # 商城表情带文字 summary。
            {"type": "image", "data": {"summary": "[嫌弃]", "sub_type": 7}},
            # "[图片]" 是平台占位符，不算表情包。
            {"type": "image", "data": {"summary": "[图片]", "sub_type": 0}},
            # 兼容 camelCase 的 subType。
            {"type": "image", "data": {"summary": "", "subType": 1}},
            # 非法 sub_type 按普通图处理。
            {"type": "image", "data": {"summary": "", "sub_type": "bad"}},
        ]
    }
    assert _direct_image_kinds(_kind_event(raw)) == [
        ("sticker", "[嫌弃]"),
        ("image", ""),
        ("sticker", ""),
        ("image", ""),
    ]
    # 非 OneBot 平台没有段列表，全部退回普通图。
    assert _direct_image_kinds(_kind_event(None)) == []
    assert _direct_image_kinds(_kind_event({"message": "CQ 码字符串"})) == []


class _ProviderStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.completion = "一张图片的转述"

    async def get_provider_by_id(self, provider_id: str) -> _ProviderStub:
        return self

    async def text_chat(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(completion_text=self.completion)


class _RepoStub:
    """索引行模拟真实 upsert 语义：kind 一旦是 sticker 就保持，直发表情包
    可把先以普通图入库的同一内容升级为 sticker，并带上表情包 summary。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.saved: list[dict[str, Any]] = []
        self.policies: dict[str, str] = {}
        self.sessions: list[dict[str, Any]] = []

    async def list_group_policies(self) -> list[dict[str, Any]]:
        return [
            {"scope_id": scope_id, "mode": mode}
            for scope_id, mode in self.policies.items()
        ]

    async def remember_session(self, *, scope_id: str, display_name: str) -> None:
        self.sessions.append({"scope_id": scope_id, "display_name": display_name})

    async def upsert_image_cache_entry(
        self,
        *,
        file_hash: str,
        file_path: str,
        message_id: str = "",
        scope_type: str = "",
        scope_id: str = "",
        file_size: int = 0,
        kind: str = "image",
        summary: str = "",
    ) -> None:
        row = self.rows.get(file_hash)
        if row is None:
            self.rows[file_hash] = {
                "file_hash": file_hash,
                "file_path": file_path,
                "kind": kind,
                "summary": str(summary or "").strip(),
                "transcription": "",
            }
        else:
            row["file_path"] = file_path
            if kind == "sticker" or row.get("kind") == "sticker":
                row["kind"] = "sticker"
                if kind == "sticker" and str(summary or "").strip():
                    row["summary"] = str(summary or "").strip()

    async def get_image_cache_entry(
        self, *, file_hash: str = "", file_path: str = ""
    ) -> dict[str, Any] | None:
        if file_hash:
            row = self.rows.get(file_hash)
        else:
            row = next(
                (r for r in self.rows.values() if r["file_path"] == file_path),
                None,
            )
        return dict(row) if row else None

    async def list_image_cache_entries(
        self, *, limit: int = 0, kind: str = ""
    ) -> list[dict[str, Any]]:
        selected = sorted(
            (
                dict(row)
                for row in self.rows.values()
                if not kind or row["kind"] == kind
            ),
            key=lambda row: row.get("last_hit_at", 0),
        )
        return selected[:limit] if limit > 0 else selected

    async def delete_image_cache_entries(self, file_hashes: list[str]) -> None:
        for file_hash in file_hashes:
            self.rows.pop(file_hash, None)

    async def save_image_transcription(
        self, *, file_hash: str, kind: str, transcription: str
    ) -> None:
        self.saved.append(
            {"file_hash": file_hash, "kind": kind, "transcription": transcription}
        )
        row = self.rows.get(file_hash)
        if row is not None:
            row["transcription"] = transcription
            row["kind"] = kind


def _plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository: _RepoStub,
    provider: _ProviderStub,
) -> HumanizePlugin:
    from astrbot.core.utils import astrbot_path

    monkeypatch.setattr(
        astrbot_path, "get_astrbot_plugin_data_path", lambda: str(tmp_path)
    )
    plugin = HumanizePlugin(SimpleNamespace(), {"memory_enabled": False})
    plugin._plugin_config = replace(
        plugin._plugin_config, image_transcription_provider_id="prov-1"
    )
    plugin._image_store = ImageCacheStore(plugin._plugin_config, repository)
    plugin._container = SimpleNamespace(repository=repository)
    plugin.context.provider_manager = provider
    return plugin


class _PlatformEvent(AstrMessageEvent):
    """aiocqhttp 形状的最小事件：send 可观察，其余全部走基类实现。"""

    def __init__(self, message_str, message_obj, platform_meta, session_id):
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.sent_chains: list[MessageChain] = []

    async def send(self, message: MessageChain) -> None:
        self.sent_chains.append(message)
        self._has_send_oper = True


def _image_event(
    *,
    segments: list[dict[str, Any]],
    image_paths: list[Path],
    quoted: bool = False,
    message_id: str = "m-1",
) -> _PlatformEvent:
    """按段顺序生成真实 Image 组件（file 指向本地源文件），可包进引用链。"""
    message_obj = AstrBotMessage()
    message_obj.type = MessageType.GROUP_MESSAGE
    message_obj.self_id = "bot-1"
    message_obj.session_id = "100"
    message_obj.message_id = message_id
    message_obj.group = SimpleNamespace(group_id="100")
    message_obj.sender = MessageMember(user_id="1001", nickname="小明")

    chain: list[Any] = []
    path_index = 0
    for segment in segments:
        if segment.get("type") != "image":
            continue
        component = Image(file=str(image_paths[path_index]))
        path_index += 1
        if quoted:
            chain.append(Reply(id="quoted-1", chain=[component]))
        else:
            chain.append(component)
    message_obj.message = chain
    message_obj.message_str = "[图片]"
    message_obj.timestamp = 0
    event = _PlatformEvent("", message_obj, _PLATFORM_META, "100")
    event.message_obj.raw_message = {"message": segments}
    event.is_wake = True
    event.is_at_or_wake_command = True
    return event


def test_transcribe_one_image_sticker_miss_saves_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        plugin = _plugin(tmp_path, monkeypatch, _RepoStub(), _ProviderStub())
        provider = plugin.context.provider_manager
        entry = {
            "file_hash": "hash-1",
            "file_path": str(tmp_path / "cache.png"),
            "kind": "sticker",
            "transcription": "",
        }
        plugin._container.repository.rows["hash-1"] = dict(entry)

        text = await plugin._transcribe_one_image(
            str(tmp_path / "cache.png"), "看这个", kind="image"
        )

        assert text == "一张图片的转述"
        call = provider.calls[0]
        assert call["prompt"].startswith(_STICKER_TRANSCRIPTION_PROMPT)
        assert call["prompt"].endswith("用户当前消息：看这个")
        assert call["image_urls"] == [str(tmp_path / "cache.png")]
        assert plugin._container.repository.saved == [
            {
                "file_hash": "hash-1",
                "kind": "sticker",
                "transcription": "一张图片的转述",
            }
        ]

    asyncio.run(scenario())


def test_transcribe_one_image_sticker_hit_skips_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        plugin = _plugin(tmp_path, monkeypatch, _RepoStub(), _ProviderStub())
        provider = plugin.context.provider_manager
        plugin._container.repository.rows["hash-1"] = {
            "file_hash": "hash-1",
            "file_path": str(tmp_path / "cache.png"),
            "kind": "sticker",
            "transcription": "缓存里的表情包转述",
        }

        text = await plugin._transcribe_one_image(
            str(tmp_path / "cache.png"), "看这个", kind="sticker"
        )

        assert text == "缓存里的表情包转述"
        assert provider.calls == []
        assert plugin._container.repository.saved == []

    asyncio.run(scenario())


def test_transcribe_one_image_regular_image_never_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        plugin = _plugin(tmp_path, monkeypatch, _RepoStub(), _ProviderStub())
        provider = plugin.context.provider_manager

        text = await plugin._transcribe_one_image(str(tmp_path / "photo.png"), "")

        assert text == "一张图片的转述"
        call = provider.calls[0]
        assert call["prompt"].startswith(_IMAGE_TRANSCRIPTION_PROMPT)
        assert not call["prompt"].endswith("用户当前消息：")
        assert plugin._container.repository.saved == []

    asyncio.run(scenario())


def test_transcribe_one_image_without_index_row_fails_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缓存禁用等场景没有索引行：照常转述，但绝不回写缓存。"""

    async def scenario() -> None:
        plugin = _plugin(tmp_path, monkeypatch, _RepoStub(), _ProviderStub())
        provider = plugin.context.provider_manager

        text = await plugin._transcribe_one_image(
            str(tmp_path / "s.png"), "", kind="sticker"
        )

        assert text == "一张图片的转述"
        assert provider.calls[0]["prompt"].startswith(_STICKER_TRANSCRIPTION_PROMPT)
        assert plugin._container.repository.saved == []

    asyncio.run(scenario())


def test_transcribe_one_image_truncates_long_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        repository = _RepoStub()
        repository.rows["hash-1"] = {
            "file_hash": "hash-1",
            "file_path": str(tmp_path / "cache.png"),
            "kind": "sticker",
            "transcription": "",
        }
        provider = _ProviderStub()
        provider.completion = "长" * 700
        plugin = _plugin(tmp_path, monkeypatch, repository, provider)

        text = await plugin._transcribe_one_image(
            str(tmp_path / "cache.png"), "", kind="sticker"
        )

        assert len(text) == 600
        assert repository.saved[0]["transcription"] == text

    asyncio.run(scenario())


def test_transcribe_one_image_sticker_passes_summary_to_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """直发表情包的段 summary（表情名）随提示词传给转述模型。"""

    async def scenario() -> None:
        plugin = _plugin(tmp_path, monkeypatch, _RepoStub(), _ProviderStub())
        provider = plugin.context.provider_manager
        plugin._container.repository.rows["hash-1"] = {
            "file_hash": "hash-1",
            "file_path": str(tmp_path / "cache.png"),
            "kind": "sticker",
            "summary": "",
            "transcription": "",
        }

        text = await plugin._transcribe_one_image(
            str(tmp_path / "cache.png"),
            "看这个",
            kind="sticker",
            summary="[动画表情]",
        )

        assert text == "一张图片的转述"
        prompt = provider.calls[0]["prompt"]
        assert prompt.startswith(_STICKER_TRANSCRIPTION_PROMPT)
        assert "表情包名称：[动画表情]" in prompt
        assert prompt.endswith("用户当前消息：看这个")

    asyncio.run(scenario())


def test_transcribe_one_image_sticker_summary_falls_back_to_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """引用/常驻工具路径没有段 summary 时，回退到索引行里存的 summary。"""

    async def scenario() -> None:
        plugin = _plugin(tmp_path, monkeypatch, _RepoStub(), _ProviderStub())
        provider = plugin.context.provider_manager
        plugin._container.repository.rows["hash-1"] = {
            "file_hash": "hash-1",
            "file_path": str(tmp_path / "cache.png"),
            "kind": "sticker",
            "summary": "[嫌弃]",
            "transcription": "",
        }

        text = await plugin._transcribe_one_image(
            str(tmp_path / "cache.png"), "", kind="image"
        )

        assert text == "一张图片的转述"
        prompt = provider.calls[0]["prompt"]
        assert prompt.startswith(_STICKER_TRANSCRIPTION_PROMPT)
        assert "表情包名称：[嫌弃]" in prompt

    asyncio.run(scenario())


def test_transcribe_one_image_regular_image_ignores_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """普通图片即使带 summary 也不拼表情包名称行。"""

    async def scenario() -> None:
        plugin = _plugin(tmp_path, monkeypatch, _RepoStub(), _ProviderStub())
        provider = plugin.context.provider_manager

        text = await plugin._transcribe_one_image(
            str(tmp_path / "photo.png"), "", kind="image", summary="[图片]"
        )

        assert text == "一张图片的转述"
        prompt = provider.calls[0]["prompt"]
        assert prompt.startswith(_IMAGE_TRANSCRIPTION_PROMPT)
        assert "表情包名称" not in prompt

    asyncio.run(scenario())


def test_prepare_direct_sticker_stores_summary_and_passes_to_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """直发表情包：summary 落进索引行，并随提示词传给转述模型。"""

    async def scenario() -> None:
        repository = _RepoStub()
        provider = _ProviderStub()
        plugin = _plugin(tmp_path, monkeypatch, repository, provider)

        sticker = tmp_path / "sticker.png"
        sticker.write_bytes(b"sticker-bytes")
        event = _image_event(
            segments=[dict(_STICKER_SEGMENT)], image_paths=[sticker], message_id="m-1"
        )
        await plugin.prepare_message_event(event)

        row = next(iter(repository.rows.values()))
        assert row["kind"] == "sticker"
        assert row["summary"] == "[动画表情]"
        prompt = provider.calls[0]["prompt"]
        assert prompt.startswith(_STICKER_TRANSCRIPTION_PROMPT)
        assert "表情包名称：[动画表情]" in prompt

    asyncio.run(scenario())


def test_transcribe_one_image_sticker_hit_ignores_current_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """命中缓存直接返回，当前观测的 summary 不触发重新转述。"""

    async def scenario() -> None:
        plugin = _plugin(tmp_path, monkeypatch, _RepoStub(), _ProviderStub())
        provider = plugin.context.provider_manager
        plugin._container.repository.rows["hash-1"] = {
            "file_hash": "hash-1",
            "file_path": str(tmp_path / "cache.png"),
            "kind": "sticker",
            "summary": "[嫌弃]",
            "transcription": "缓存里的表情包转述",
        }

        text = await plugin._transcribe_one_image(
            str(tmp_path / "cache.png"), "", kind="sticker", summary="[动画表情]"
        )

        assert text == "缓存里的表情包转述"
        assert provider.calls == []

    asyncio.run(scenario())


def test_transcribe_one_image_sticker_without_any_summary_omits_name_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """索引行与直发参数都没有 summary 时，提示词不拼表情包名称行。"""

    async def scenario() -> None:
        plugin = _plugin(tmp_path, monkeypatch, _RepoStub(), _ProviderStub())
        provider = plugin.context.provider_manager
        plugin._container.repository.rows["hash-1"] = {
            "file_hash": "hash-1",
            "file_path": str(tmp_path / "cache.png"),
            "kind": "sticker",
            "summary": "",
            "transcription": "",
        }

        text = await plugin._transcribe_one_image(
            str(tmp_path / "cache.png"), "", kind="sticker"
        )

        assert text == "一张图片的转述"
        prompt = provider.calls[0]["prompt"]
        assert prompt.startswith(_STICKER_TRANSCRIPTION_PROMPT)
        assert "表情包名称" not in prompt

    asyncio.run(scenario())


def test_prepare_silent_group_skips_images_and_transcription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """完全沉默的群：不落图、不转述、不调用任何外部接口。"""

    async def scenario() -> None:
        repository = _RepoStub()
        repository.policies["global"] = "silent"
        provider = _ProviderStub()
        plugin = _plugin(tmp_path, monkeypatch, repository, provider)

        sticker = tmp_path / "sticker.png"
        sticker.write_bytes(b"sticker-bytes")
        event = _image_event(segments=[dict(_STICKER_SEGMENT)], image_paths=[sticker])
        await plugin.prepare_message_event(event)

        assert provider.calls == []
        assert repository.rows == {}
        assert event.get_extra(_EVENT_IMAGE_CACHE_PATHS_KEY, ()) == ()

    asyncio.run(scenario())


def test_prepare_classifies_per_image_and_reuses_sticker_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """直发图逐张分类转述：表情包第二次不再调模型，普通图每次现转。"""

    async def scenario() -> None:
        repository = _RepoStub()
        provider = _ProviderStub()
        plugin = _plugin(tmp_path, monkeypatch, repository, provider)

        sticker = tmp_path / "sticker.png"
        sticker.write_bytes(b"sticker-bytes")
        photo = tmp_path / "photo.png"
        photo.write_bytes(b"photo-bytes")
        segments = [
            {"type": "text", "data": {"text": "看图"}},
            dict(_STICKER_SEGMENT),
            dict(_PHOTO_SEGMENT),
        ]

        event = _image_event(
            segments=segments, image_paths=[sticker, photo], message_id="m-1"
        )
        await plugin.prepare_message_event(event)

        kinds = sorted(row["kind"] for row in repository.rows.values())
        assert kinds == ["image", "sticker"]
        assert len(provider.calls) == 2
        assert provider.calls[0]["prompt"].startswith(_STICKER_TRANSCRIPTION_PROMPT)
        assert provider.calls[1]["prompt"].startswith(_IMAGE_TRANSCRIPTION_PROMPT)
        assert len(repository.saved) == 1
        cache_paths = list(event.get_extra(_EVENT_IMAGE_CACHE_PATHS_KEY, ()))
        assert len(cache_paths) == 2
        transcriptions = list(event.get_extra(_EVENT_IMAGE_TRANSCRIPTIONS_KEY, ()))
        assert len(transcriptions) == 2
        assert all("ImageCache(" not in item for item in transcriptions)
        # 组件路径已改写为缓存路径，转述与图片一一对应。
        assert event.message_obj.message[0].path == cache_paths[0]

        # 同一张表情包再次发送：命中缓存零调用；普通图照常现转。
        resent = _image_event(
            segments=segments, image_paths=[sticker, photo], message_id="m-2"
        )
        await plugin.prepare_message_event(resent)
        assert len(provider.calls) == 3
        resent_transcriptions = list(
            resent.get_extra(_EVENT_IMAGE_TRANSCRIPTIONS_KEY, ())
        )
        assert resent_transcriptions[0] == transcriptions[0]

    asyncio.run(scenario())


def test_prepare_quoted_sticker_hits_cache_via_index(tmp_path: Path) -> None:
    """引用消息里的表情包按内容 hash 反查索引，同样命中长期缓存。"""

    async def scenario() -> None:
        repository = _RepoStub()
        provider = _ProviderStub()
        plugin = _plugin(tmp_path, pytest.MonkeyPatch(), repository, provider)

        sticker = tmp_path / "sticker.png"
        sticker.write_bytes(b"sticker-bytes")
        digest = hashlib.sha256(b"sticker-bytes").hexdigest()
        cache_path = tmp_path / "image_cache" / f"{digest}.png"
        repository.rows[digest] = {
            "file_hash": digest,
            "file_path": str(cache_path),
            "kind": "sticker",
            "transcription": "缓存里的表情包转述",
        }
        segments = [
            {"type": "reply", "data": {"id": "42"}},
            dict(_STICKER_SEGMENT),
        ]

        event = _image_event(
            segments=segments, image_paths=[sticker], quoted=True, message_id="m-1"
        )
        await plugin.prepare_message_event(event)

        assert provider.calls == []
        assert repository.saved == []
        transcriptions = list(event.get_extra(_EVENT_IMAGE_TRANSCRIPTIONS_KEY, ()))
        assert transcriptions == ["缓存里的表情包转述"]

    asyncio.run(scenario())
