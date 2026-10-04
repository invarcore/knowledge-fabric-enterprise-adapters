# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""CLI entrypoint for the private adapter layer."""

from enterprise_adapters.contracts import AdapterContext


def main() -> int:
    context = AdapterContext(adapter_name="enterprise-adapters", environment="local")
    print(f"Phase 3 adapter layer initialized for {context.environment}.")
    print("Read-only discovery tools should come before any write-capable action.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
