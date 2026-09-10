"""核心注入的图片附件占位行必须对多模态请求也摘掉。

核心把用户图片压缩成 ``data/temp/compressed_*.jpg``，并把该临时路径写进
``extra_user_content_parts``。临时文件在事件结束后约 2 分钟被清理，模型若在
后续回合引用它必然读不到——线上就是这么失败的（模型拿着
``/workspace/<id>/AstrBot/data/temp/compressed_*.jpg`` 去调生图工具）。
插件自己的标注已经给出持久缓存路径，所以这行必须被摘掉，且不能顺手把
多模态需要的 ``image_urls`` 一起清掉。
"""

from __future__ import annotations

from types import SimpleNamespace

from astrbot_plugin_humanize.main import (
    _TEMPORARY_IMAGE_PATH,
    _strip_image_attachment_parts,
)

from astrbot.core.agent.message import TextPart


def _request(*texts: str, image_urls: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        extra_user_content_parts=[TextPart(text=text) for text in texts],
        image_urls=list(image_urls if image_urls is not None else []),
    )


def test_multimodal_keeps_image_urls_but_drops_temp_path() -> None:
    request = _request(
        "用户文字",
        "[Image Attachment: path /home/lovie/AstrBot/data/temp/compressed_x.jpg]",
        image_urls=["/home/lovie/AstrBot/data/temp/compressed_x.jpg"],
    )
    _strip_image_attachment_parts(request, drop_image_urls=False)
    assert [part.text for part in request.extra_user_content_parts] == ["用户文字"]
    # 多模态仍要靠 image_urls 把图喂给视觉模型。
    assert request.image_urls == ["/home/lovie/AstrBot/data/temp/compressed_x.jpg"]


def test_non_multimodal_also_drops_image_urls() -> None:
    request = _request(
        "[Image Attachment: path /tmp/compressed_x.jpg]",
        image_urls=["/tmp/compressed_x.jpg"],
    )
    _strip_image_attachment_parts(request, drop_image_urls=True)
    assert request.extra_user_content_parts == []
    assert request.image_urls == []


def test_quoted_message_attachment_is_dropped_too() -> None:
    request = _request(
        "[Image Attachment in quoted message: path /tmp/compressed_q.jpg]",
        "保留这行",
    )
    _strip_image_attachment_parts(request)
    assert [part.text for part in request.extra_user_content_parts] == ["保留这行"]


def test_parts_without_text_attribute_survive() -> None:
    """非文本 part 不能被 getattr 兜底逻辑误删。"""
    request = _request("普通文本")
    keeper = SimpleNamespace(no_text=True)
    request.extra_user_content_parts.append(keeper)
    _strip_image_attachment_parts(request, drop_image_urls=True)
    assert request.extra_user_content_parts == [
        TextPart(text="普通文本"),
        keeper,
    ]


def test_missing_parts_attribute_is_tolerated() -> None:
    request = SimpleNamespace(image_urls=["/tmp/x.jpg"])
    _strip_image_attachment_parts(request)
    assert request.extra_user_content_parts == []


def test_temporary_image_path_pattern() -> None:
    """只认核心压缩出来的中间文件，别把普通 temp 文件也算进去。"""
    assert _TEMPORARY_IMAGE_PATH.search(
        "/home/lovie/AstrBot/data/temp/compressed_20260909142325403_0d5c.jpg"
    )
    assert _TEMPORARY_IMAGE_PATH.search(
        r"D:\AstrBot\data\temp\compressed_20260909142325403.jpg"
    )
    assert not _TEMPORARY_IMAGE_PATH.search(
        "/home/lovie/AstrBot/data/temp/other_20260909142325403.jpg"
    )
    assert not _TEMPORARY_IMAGE_PATH.search(
        "/home/lovie/AstrBot/data/plugin_data/astrbot_plugin_humanize/image_cache/a.jpg"
    )
