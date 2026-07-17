# OpenViking Source Record

- Upstream: https://github.com/volcengine/OpenViking
- Tag: `v0.4.9`
- Commit: `4f0bd86f32c5a98ed78e7ba04adb5708c0bdb89a`
- License: GNU Affero General Public License v3.0

## Included Upstream Files

- `openviking/core/identifiers.py`
- `openviking/core/peer_id.py`
- `openviking/message/part.py`
- `openviking/retrieve/memory_lifecycle.py`
- `openviking/session/memory/dataclass.py`
- `openviking/session/memory/merge_op/base.py`
- `openviking/session/memory/utils/link_renderer.py`

## Modifications

- Moved sources into the private `humanize.vendor.openviking_core` namespace.
- Rewrote upstream package imports as private relative imports.
- Reduced `core/namespace.py` to the URI segment helpers required by retained code.
- Applied the host project's Python 3.12 type-annotation and Ruff formatting rules.
- Added minimal package initializers and upstream version constants.

No OpenViking native libraries or `third_party/` sources are included in this trimmed core.
