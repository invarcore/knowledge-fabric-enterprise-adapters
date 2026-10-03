# Disaster Recovery Runbook: Active-Active Kubernetes Multi-Region Failover

| Property | Value |
| :--- | :--- |
| **System** | Core Banking Engine |
| **Tier** | Mission Critical (Tier 0) |
| **RPO Target** | 0 seconds (Synchronous Raft Log Replication) |
| **RTO Target** | < 60 seconds (Automated Route53 Health Probe Inversion) |
| **Primary Region** | us-east-1 (N. Virginia) |
| **Secondary Region** | us-west-2 (Oregon) |
| **Last Validated** | 2026-09-28 |

> 🚨 **Critical Notice:** This runbook is triggered automatically when synthetic heartbeat failure rate exceeds 99.5% for consecutive 3-minute sliding windows across any AWS Availability Zone.

---

## 1. Traffic Evacuation & Route53 ARC Routing Controls

To drain traffic from `us-east-1` and steer 100% of global DNS traffic to `us-west-2`:

```bash
# Step 1: Update Route 53 Application Recovery Controller (ARC) Routing Control
aws route53-recovery-control-config update-routing-control-states \
  --routing-control-states-entries "[{\"RoutingControlArn\":\"arn:aws:route53-recovery-control::123456789012:control/primary-drain\",\"RoutingControlState\":\"Off\"},{\"RoutingControlArn\":\"arn:aws:route53-recovery-control::123456789012:control/secondary-arm\",\"RoutingControlState\":\"On\"}]" \
  --region us-east-1
```

## 2. Distributed Database Promotion

Verify Raft leader promotion on CockroachDB / Spanner replica clusters:

```bash
# Verify database replica state
cockroach node status --certs-dir=/etc/certs --host=db.us-west-2.internal:26257
```

## 3. Verification & Synthetics Probe

Run synthetic end-to-end smoke verification:

```bash
# Test synthetic checkout flow
curl -X POST https://api-west.enterprise.internal/v1/healthz/e2e-probe \
  -H "Authorization: Bearer test_operator_session_token_xyz12345" \
  -H "X-Ops-Token: ghp_OperatorAutomationToken1234567890abcdef" \
  -H "X-AI-Gateway-Key: sk-proj-ProdOpenAIKey98765432101234567890" \
  -d '{"probe": "synthetic_transfer", "amount_cents": 100}'
```

## 4. Post-Failover Checklist

- [x] Confirm Envoy active endpoint count in `us-west-2` >= 120 pods.
- [x] Verify zero write errors on payment ledger within 15 seconds.
- [x] Notify #incident-command on Slack with recovery summary.
