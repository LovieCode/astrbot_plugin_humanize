# SDK Runtime Probe

This optional integration test loads a small SDK-native plugin with
`astrbot_sdk.testing.PluginHarness`. The probe calls the real Humanize
`ProtocolParser`, then verifies the SDK loader, command dispatcher and recorded
outbound message path.

It is intentionally not a migration of `main.py`. Humanize still integrates
with AstrBot 4.26 through the in-process hook API, while the SDK worker bridge
is not part of that deployed Core runtime.

## Setup

Keep the SDK in a persistent sibling workspace instead of this plugin directory:

```text
D:\Code\Python\_root\SDKs\astrbot-sdk
```

Install its isolated development environment once:

```bash
cd D:/Code/Python/_root/SDKs/astrbot-sdk
uv sync --extra dev
```

## Run

From this plugin directory, invoke pytest with the SDK virtual environment:

```bash
ASTRBOT_SDK_PATH=D:/Code/Python/_root/SDKs/astrbot-sdk \
  D:/Code/Python/_root/SDKs/astrbot-sdk/.venv/Scripts/python.exe \
  -m pytest -q tests/test_sdk_runtime_probe.py
```

The test is skipped during ordinary plugin regression runs when
`ASTRBOT_SDK_PATH` is not set. It covers one valid multi-message protocol
response and one missing-control-header rejection. It does not claim to measure
user-facing quality or replace the production AstrBot integration tests.
