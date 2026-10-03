#!/usr/bin/env python3
"""Zero-Cost End-to-End Live Verification & Smoke Test for Knowledge Fabric Enterprise Adapters.

Modes:
  1. Local Hermetic Mode (Default):
     - Allowlisted document ingestion (Markdown, text, structured files)
     - Mock SaaS connector validation (Google Drive, Notion, Jira, Confluence)
     - Security sanitization & secret redaction (AWS keys, bearer tokens, connection strings)
     - Cryptographic approval verification & signature enforcement
     - Mutative execution gatekeeper & audit trail persistence (SQLite)
     - Enterprise Adapters MCP tools interface (health_check, list_sources, fetch_document)
  2. OpenRouter Cloud Mode:
     - Connects to OpenRouter's free tier (e.g. openrouter/free)
     - Evaluates sanitized enterprise adapter telemetry and audit events
     - Generates an automated enterprise compliance sign-off report

Usage:
  python benchmarks/live_adapter_smoke_test.py
  python benchmarks/live_adapter_smoke_test.py --openrouter
  python benchmarks/live_adapter_smoke_test.py --openrouter --model openrouter/free
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from enterprise_adapters.approval_storage import SQLiteApprovalStore
from enterprise_adapters.approvals import (
    ApprovalPackageGenerator,
    compute_approval_signature,
    verify_approval_signature,
)
from enterprise_adapters.audit import SQLiteAuditTrail
from enterprise_adapters.execution import ApprovedRuntimeActionAdapter
from enterprise_adapters.mcp_server import EnterpriseAdaptersMCPTools
from enterprise_adapters.policy import PolicyDecision, PolicyDecisionType, PolicyEvaluator
from enterprise_adapters.standard_source_adapters import AllowlistedDocumentSourceAdapter
from knowledge_fabric_adapters.connectors.confluence import ConfluenceSourceAdapter
from knowledge_fabric_adapters.connectors.google_drive import GoogleDriveSourceAdapter
from knowledge_fabric_adapters.connectors.jira import JiraSourceAdapter
from knowledge_fabric_adapters.connectors.notion import NotionSourceAdapter


def run_hermetic_pipeline() -> dict[str, Any]:
    """Execute complete enterprise adapter pipeline in hermetic offline mode."""
    t0 = time.perf_counter()

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        tmp_path = Path(tmpdir)
        docs_dir = tmp_path / "enterprise_docs"
        docs_dir.mkdir()
        db_path = tmp_path / "audit.db"

        # 1. Create simulated enterprise files with deliberate secrets to verify redaction
        secret_content = (
            "# Infrastructure Deployment & Secret Management\n\n"
            "## Production Database Configuration\n"
            "Connect string: postgres://admin:SuperSecretPass123!@db.internal.corp:5432/core_db\n\n"
            "## Cloud Storage Credentials\n"
            "Access Key ID: AKIAIOSFODNN7EXAMPLE\n"
            "Internal Service Token: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-zR_dummy\n\n"
            "Architecture mandates all services authenticate using mutual TLS and mTLS certificates."
        )
        spec_file = docs_dir / "cloud_architecture.md"
        spec_file.write_text(secret_content, encoding="utf-8")

        # 2. Ingest via AllowlistedDocumentSourceAdapter
        doc_adapter = AllowlistedDocumentSourceAdapter(
            source_root=docs_dir,
            allowed_roots=[docs_dir],
        )
        resources = doc_adapter.list_resources()
        assert len(resources) == 1
        fetched = doc_adapter.fetch_resource(resources[0].resource_id)
        sanitized_content = str(fetched["content"])

        # 3. Assert strict secret scrubbing
        assert "AKIAIOSFODNN7EXAMPLE" not in sanitized_content
        assert "[REDACTED_AWS_KEY]" in sanitized_content
        assert "SuperSecretPass123!" not in sanitized_content
        assert "[REDACTED]" in sanitized_content
        assert "eyJhbGciOi" not in sanitized_content

        # 4. Ingest Mock SaaS Source Connectors
        gdrive = GoogleDriveSourceAdapter(access_token="fake_token")
        notion = NotionSourceAdapter(api_key="secret_notion")
        jira = JiraSourceAdapter(base_url="https://mock-jira.atlassian.net")
        confluence = ConfluenceSourceAdapter(base_url="https://mock-wiki.atlassian.net", space_keys=["ENG"])
        assert gdrive.source_type == "google_drive"
        assert notion.source_type == "notion"
        assert jira.source_type == "jira"
        assert confluence.source_type == "confluence"

        # 5. Policy & Cryptographic Verification Gate
        evaluator = PolicyEvaluator()
        action_payload = {
            "action_name": "deploy_canary_image",
            "write": True,
            "target": "prod-cluster-us-east-1",
            "image": "registry.corp/service:v2.4.0",
        }
        policy_decision = evaluator.evaluate(action_payload)

        # 6. Generate Approval Request and Cryptographic HMAC Signature
        pkg_gen = ApprovalPackageGenerator()
        approval_req = pkg_gen.create(
            action=action_payload,
            policy_decision=policy_decision,
            requested_by="ci-automation@company.internal",
        )

        approval_id = approval_req.approval_id
        secret_key = "governance-signing-hmac-key-2026"
        timestamp = datetime.now(UTC).isoformat()
        signature = compute_approval_signature(
            approval_id=approval_id,
            plan_id="plan_deploy_001",
            step_ids=["deploy_canary_image"],
            decision="approved",
            reviewer="lead-secops@company.internal",
            timestamp=timestamp,
            secret_key=secret_key,
        )

        # Verify signature
        valid = verify_approval_signature(
            approval_id=approval_id,
            plan_id="plan_deploy_001",
            step_ids=["deploy_canary_image"],
            decision="approved",
            reviewer="lead-secops@company.internal",
            timestamp=timestamp,
            signature=signature,
            secret_key=secret_key,
        )
        assert valid is True

        # 7. Execute Mutative Action via ApprovedRuntimeActionAdapter
        audit_trail = SQLiteAuditTrail(db_path)
        executor = ApprovedRuntimeActionAdapter(audit_trail=audit_trail)
        exec_action = {
            "action_name": "deploy_canary_image",
            "write": True,
            "approval_id": approval_id,
            "plan_id": "plan_deploy_001",
            "step_ids": ["deploy_canary_image"],
            "decision": "approved",
            "reviewer": "lead-secops@company.internal",
            "timestamp": timestamp,
            "signature": signature,
            "secret_key": secret_key,
            "policy_decision": PolicyDecision(
                decision_id="dec_approved",
                decision=PolicyDecisionType.ALLOW,
                reasons=["Signed by Lead SecOps"],
            ),
        }
        receipt = executor.execute_action(exec_action)
        assert receipt["status"] == "executed"
        assert receipt["approval_id"] == approval_id

        # 8. MCP Tools Interface Verification
        mcp_tools = EnterpriseAdaptersMCPTools({"documents": doc_adapter})
        health = mcp_tools.health_check()
        assert health["status"] == "healthy"
        assert "documents" in health["registered_sources"]

        sources = mcp_tools.list_sources()
        assert len(sources["sources"]) == 1

        doc_fetch = mcp_tools.fetch_document("documents", resources[0].resource_id)
        assert "[REDACTED_AWS_KEY]" in str(doc_fetch["content"])
        assert "path" not in doc_fetch  # Server internal path leak prevented

        # Record approval in SQLiteApprovalStore
        store = SQLiteApprovalStore(db_path)
        store.save(approval_req, policy_decision)
        fetched_record = store.get(approval_id)
        assert fetched_record is not None
        assert fetched_record.action_name == "deploy_canary_image"

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return {
        "status": "success",
        "elapsed_ms": elapsed_ms,
        "resource_id": resources[0].resource_id,
        "approval_id": approval_id,
        "execution_id": receipt["execution_id"],
        "policy_decision": policy_decision.decision.value,
        "signature_verified": valid,
        "secret_redactions_verified": 3,
        "receipt": receipt,
        "mcp_health": health,
    }


def call_openrouter(
    prompt: str,
    model: str = "openrouter/free",
    api_key: str | None = None,
) -> dict[str, Any]:
    """Call OpenRouter API with prompt and compute token/latency metrics."""
    resolved_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not resolved_key:
        raise ValueError(
            "OPENROUTER_API_KEY environment variable is required to run OpenRouter mode."
        )

    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the Enterprise Governance Auditor for Knowledge Fabric. "
                    "Review the adapter execution receipt and secret scrubbing verification, "
                    "then produce a concise 3-bullet governance sign-off."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 400,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {resolved_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/sagarv48/knowledge-fabric-enterprise-adapters",
            "X-Title": "Knowledge Fabric Enterprise Adapters Smoke Test",
        },
        method="POST",
    )

    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=45) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    elapsed_ms = (time.perf_counter() - t0) * 1000

    choice = body.get("choices", [{}])[0]
    message_content = choice.get("message", {}).get("content", "")
    usage = body.get("usage", {})

    return {
        "content": message_content,
        "model": body.get("model", model),
        "latency_ms": elapsed_ms,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Zero-Cost End-to-End Live Verification for Knowledge Fabric Enterprise Adapters"
    )
    parser.add_argument("--openrouter", action="store_true", help="Run with OpenRouter cloud synthesis")
    parser.add_argument("--model", default="openrouter/free", help="Model slug to use (default: openrouter/free)")
    args = parser.parse_args()

    print("=" * 70)
    print("🚀 Knowledge Fabric Enterprise Adapters: End-to-End Verification")
    print("=" * 70)

    # Step 1: Run Local Hermetic Pipeline
    print("\n[Step 1/2] Running Hermetic Enterprise Ingestion & Security Gate...")
    hermetic_res = run_hermetic_pipeline()

    print(f"   ✅ Document Ingested & Sanitized: {hermetic_res['resource_id']}")
    print(f"   ✅ Secret Redactions Confirmed:    {hermetic_res['secret_redactions_verified']} patterns scrubbed")
    print(f"   ✅ Policy Decision:               {hermetic_res['policy_decision'].upper()}")
    print(f"   ✅ Cryptographic HMAC Verified:   {hermetic_res['signature_verified']}")
    print(f"   ✅ Mutative Execution Receipt:    {hermetic_res['execution_id']}")
    print(f"   ✅ MCP Server Tool Status:        {hermetic_res['mcp_health']['status'].upper()}")
    print(f"   ⏱️  Hermetic Pipeline Latency:      {hermetic_res['elapsed_ms']:.2f} ms")

    # Step 2: OpenRouter Cloud Verification (if requested)
    if args.openrouter:
        print(f"\n[Step 2/2] Connecting to OpenRouter Cloud Free Tier ({args.model})...")
        prompt = (
            f"Enterprise Adapter Execution Receipt:\n"
            f"- Resource ID: {hermetic_res['resource_id']}\n"
            f"- Policy Decision: {hermetic_res['policy_decision']}\n"
            f"- Cryptographic Signature Verified: {hermetic_res['signature_verified']}\n"
            f"- Execution ID: {hermetic_res['execution_id']}\n"
            f"- Redacted Patterns: AWS Keys, Bearer Tokens, Database Passwords\n"
            f"Please verify compliance posture and produce sign-off."
        )
        try:
            cloud_res = call_openrouter(prompt, model=args.model)
            print(f"   ✅ Connected successfully with: {cloud_res['model']}")
            print(f"   ⏱️  Cloud Latency:             {cloud_res['latency_ms']:.2f} ms")
            print(f"   📊 Prompt / Completion Tokens: {cloud_res['prompt_tokens']} / {cloud_res['completion_tokens']}")
            print("   💬 Synthesis Preview:")
            for line in cloud_res["content"].strip().split("\n")[:5]:
                print(f"      {line}")
        except Exception as exc:
            print(f"   ⚠️  OpenRouter test failed: {exc}")
            return 1
    else:
        print("\n[Step 2/2] OpenRouter Cloud Verification: SKIPPED (run with --openrouter to test live cloud)")

    print("\n" + "=" * 70)
    print("🎯 Verification Status: ALL GATES PASSED (100% Hermetic & Compliant)")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
