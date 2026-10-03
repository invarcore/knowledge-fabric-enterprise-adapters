#!/usr/bin/env python3
"""
demo/02-demo.py — End-to-end Path A demo

Full flow:
  1. User types a question about Kubernetes
  2. knowledge-fabric retrieves relevant evidence from K8s docs (lexical retrieval)
  3. intent-fabric (Foundry Local / mistral) generates a real AI plan
  4. Policy check → requires_approval for write actions
  5. User approves → GitHub issue is created in sagarv48/knowledge-fabric-demo

Usage:
  cd knowledge-fabric-enterprise-adapters
  python3 -m pip install -e ".[dev]"
  python3 demo/02-demo.py

Required environment variables:
  GITHUB_TOKEN          GitHub PAT with Issues: write on sagarv48/knowledge-fabric-demo
  GITHUB_ISSUE_REPO     sagarv48/knowledge-fabric-demo
  INTENT_PLANNER        foundry  (or omit to use rule-based fallback)
  FOUNDRY_BASE_URL      http://127.0.0.1:61633  (default)
  FOUNDRY_PLAN_MODEL    mistral-nemo-12b-instruct-generic-gpu  (default)

  # knowledge-fabric database (matches docker-compose defaults)
  KF_DB_HOST            localhost
  KF_DB_PORT            5432
  KF_DB_NAME            knowledge_fabric
  KF_DB_USER            knowledge_fabric
  KF_DB_PASSWORD        (set in your .env)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Allow running without installing if repos are siblings
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent.parent
sys.path.insert(0, str(_HERE.parent / "knowledge-fabric" / "src"))
sys.path.insert(0, str(_HERE.parent / "intent-fabric" / "src"))
sys.path.insert(0, str(_HERE / "src"))


# ---------------------------------------------------------------------------
# Lazy imports — clear error messages if a repo is not installed
# ---------------------------------------------------------------------------
def _import_knowledge_fabric():
    try:
        from knowledge_fabric.config import load_settings
        from knowledge_fabric.db import create_postgres_connection_factory
        from knowledge_fabric.embeddings import MockEmbeddingProvider
        from knowledge_fabric.retrieval.pipeline import RetrievalPipeline
        from knowledge_fabric.retrieval.postgres import PostgresRetrievalStore
        return load_settings, create_postgres_connection_factory, MockEmbeddingProvider, RetrievalPipeline, PostgresRetrievalStore
    except ImportError as exc:
        print(f"\n[ERROR] knowledge-fabric not found: {exc}")
        print("  Run: cd ../knowledge-fabric && pip install -e .[dev]")
        sys.exit(1)


def _import_intent_fabric():
    try:
        from intent_fabric.models import IntentRequest, EvidencePackageReference, EvidenceItemReference
        from intent_fabric.planning import build_planner, RuleBasedPlanner
        from intent_fabric.policies import PolicyEngine
        from intent_fabric.approvals import ApprovalPackageGenerator
        return IntentRequest, EvidencePackageReference, EvidenceItemReference, build_planner, RuleBasedPlanner, PolicyEngine, ApprovalPackageGenerator
    except ImportError as exc:
        print(f"\n[ERROR] intent-fabric not found: {exc}")
        print("  Run: cd ../intent-fabric && pip install -e .[dev]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Evidence retrieval (knowledge-fabric)
# ---------------------------------------------------------------------------
def retrieve_evidence(query: str, top_k: int = 5):
    load_settings, create_postgres_connection_factory, MockEmbeddingProvider, RetrievalPipeline, PostgresRetrievalStore = _import_knowledge_fabric()

    # Build a minimal settings-like object from env vars
    class _DB:
        host = os.environ.get("KF_DB_HOST", "localhost")
        port = int(os.environ.get("KF_DB_PORT", "5432"))
        name = os.environ.get("KF_DB_NAME", "knowledge_fabric")
        user = os.environ.get("KF_DB_USER", "knowledge_fabric")
        password = os.environ.get("KF_DB_PASSWORD", "")

    try:
        conn_factory = create_postgres_connection_factory(_DB())
        store = PostgresRetrievalStore(connection_factory=conn_factory)
        pipeline = RetrievalPipeline(
            retrieval_store=store,
            embedding_provider=MockEmbeddingProvider(),
        )
        tenant_id = os.environ.get("KF_TENANT_ID", "k8s-docs")
        package = pipeline.retrieve_evidence(query_text=query, top_k=top_k, tenant_id=tenant_id)
        return package
    except Exception as exc:
        print(f"\n[WARNING] Could not connect to knowledge-fabric database: {exc}")
        print("  Continuing with empty evidence (rule-based planner will still run).")
        return None


# ---------------------------------------------------------------------------
# Planning (intent-fabric)
# ---------------------------------------------------------------------------
def plan_from_evidence(query: str, evidence_package) -> tuple:
    IntentRequest, EvidencePackageReference, EvidenceItemReference, build_planner, RuleBasedPlanner, PolicyEngine, ApprovalPackageGenerator = _import_intent_fabric()

    # Convert KF evidence package to IF reference model
    if evidence_package is not None and evidence_package.items:
        items = [
            EvidenceItemReference(
                chunk_id=item.chunk_id,
                document_uri=item.document_uri,
                snippet=item.text[:400],
                score=item.rrf_score,
            )
            for item in evidence_package.items
        ]
    else:
        items = []

    evidence_ref = EvidencePackageReference(query_text=query, items=items)
    intent = IntentRequest(
        intent_id="demo-intent",
        user_request=query,
        requested_actions=["ticket_create"],
    )

    planner = build_planner() or RuleBasedPlanner()
    planner_name = type(planner).__name__
    print(f"\n[PLANNER] Using: {planner_name}")

    plan = planner.create_plan(intent, evidence_ref)

    policy = PolicyEngine()
    from intent_fabric.serde import to_dict
    decision = policy.evaluate(to_dict(plan))

    return plan, decision, evidence_ref


# ---------------------------------------------------------------------------
# Main demo loop
# ---------------------------------------------------------------------------
def main() -> None:
    print("=" * 70)
    print("  knowledge-fabric × intent-fabric × enterprise-adapters")
    print("  Path A Demo: K8s docs → AI plan → GitHub Issue")
    print("=" * 70)

    # Check required env vars
    github_token = os.environ.get("GITHUB_TOKEN", "")
    github_repo = os.environ.get("GITHUB_ISSUE_REPO", "sagarv48/knowledge-fabric-demo")
    if not github_token:
        print("\n[ERROR] Set GITHUB_TOKEN before running this demo.")
        print("  export GITHUB_TOKEN=<your-pat>")
        sys.exit(1)

    # Query
    print("\nExample questions:")
    print("  - Why is my pod in CrashLoopBackOff?")
    print("  - How do I configure resource limits?")
    print("  - What is a Kubernetes ConfigMap?")
    print()
    query = input("Enter your question (or press Enter for default): ").strip()
    if not query:
        query = "Why is my pod stuck in CrashLoopBackOff and how do I fix it?"
    print(f"\n[QUERY] {query}")

    # Step 1: Retrieve evidence
    print("\n[1/5] Retrieving evidence from K8s docs...")
    evidence = retrieve_evidence(query)
    if evidence and evidence.items:
        print(f"       Found {len(evidence.items)} relevant chunks.")
        print(f"       Top result: {evidence.items[0].document_uri}")
    else:
        print("       No evidence found (DB may not be running — continuing without it).")

    # Step 2: Generate plan
    print("\n[2/5] Generating action plan...")
    plan, decision, evidence_ref = plan_from_evidence(query, evidence)
    print(f"       Plan: {plan.summary}")
    print(f"       Steps: {len(plan.steps)}")
    for step in plan.steps:
        print(f"         → [{step.action_contract.action_type}] {step.title}")

    # Step 3: Policy check
    print(f"\n[3/5] Policy decision: {decision.decision}")
    for reason in decision.reasons:
        print(f"       • {reason}")

    # Step 4: Approval
    print("\n[4/5] This action requires human approval.")
    print(f"       Plan summary: {plan.summary}")
    answer = input("       Approve and create a GitHub issue? [y/N]: ").strip().lower()
    if answer != "y":
        print("\n       Declined. No issue created. Exiting.")
        return

    # Build the issue body from evidence
    evidence_snippet = ""
    if evidence and evidence.items:
        top = evidence.items[0]
        evidence_snippet = f"\n\n**Evidence from K8s docs** (`{top.document_uri}`, score={top.rrf_score:.3f}):\n\n> {top.text[:500]}"

    issue_title = f"[AI] {plan.steps[0].title if plan.steps else query[:80]}"
    issue_body = (
        f"**Original question:** {query}\n\n"
        f"**Plan summary:** {plan.summary}\n"
        f"{evidence_snippet}\n\n"
        f"---\n*Created by knowledge-fabric enterprise adapter demo.*"
    )

    # Step 5: Execute
    print("\n[5/5] Creating GitHub issue...")

    from enterprise_adapters.policy import PolicyDecision, PolicyDecisionType
    from enterprise_adapters.approvals import ApprovalPackageGenerator
    from enterprise_adapters.github_issue_adapter import GitHubIssueAdapter

    evaluator_decision = PolicyDecision(
        decision_id="demo-policy",
        decision=PolicyDecisionType.ALLOW,
        reasons=["Human approved interactively."],
    )

    generator = ApprovalPackageGenerator()
    from enterprise_adapters.policy import PolicyDecisionType as PDT
    temp_decision = PolicyDecision(
        decision_id="demo-policy",
        decision=PDT.REQUIRES_APPROVAL,
        reasons=["Write action — requires approval."],
    )
    approval = generator.create(
        action={"action_name": "create_github_issue", "write": True},
        policy_decision=temp_decision,
        requested_by="demo-user",
    )

    action = {
        "action_name": "create_github_issue",
        "write": True,
        "approval_id": approval.approval_id,
        "policy_decision": evaluator_decision,
        "payload": {
            "title": issue_title,
            "body": issue_body,
            "labels": ["ai-generated", "k8s-docs", "needs-review"],
        },
    }

    adapter = GitHubIssueAdapter(token=github_token, repo=github_repo)
    receipt = adapter.execute_action(action)

    print(f"\n{'=' * 70}")
    print("  ✅ Issue created!")
    print(f"     URL:    {receipt['metadata']['issue_url']}")
    print(f"     Number: #{receipt['metadata']['issue_number']}")
    print(f"     Repo:   {receipt['metadata']['repo']}")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    main()
