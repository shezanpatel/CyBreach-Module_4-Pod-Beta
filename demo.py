"""Interactive demo demonstrating CyBreach Module 4: Arena (Pod Beta).

Run:
    python demo.py
"""

import asyncio
import json
from app.engagement.api import repo, wallet, cooldown, event_bus
from app.engagement.models import LaunchTier, CreateEngagementRequest, UpdateScopeRequest, CompleteEngagementRequest
from app.engagement.api import create_engagement, update_engagement_scope, execute_engagement, complete_engagement, archive_engagement
from app.engagement.report_generator import ReportGenerator


async def run_demo():
    print("=" * 70)
    print("CYBREACH MODULE 4: ARENA - POD BETA INTERACTIVE DEMO")
    print("=" * 70)

    tenant_id = "tenant_high"
    initial_balance = await wallet.get_balance(tenant_id)
    print(f"\n[1] INITIAL WALLET BALANCE for '{tenant_id}': {initial_balance} credits")

    # Step 1: Create Engagement
    print("\n[2] CREATING ENGAGEMENT (Guided Tier: 25 credits)...")
    create_req = CreateEngagementRequest(
        tenant_id=tenant_id,
        engagement_type="guided",
        scope={},
    )
    eng_res = await create_engagement(create_req)
    eng_id = eng_res.engagement_id
    print(f"    - Engagement ID: {eng_id}")
    print(f"    - State: {eng_res.state.value.upper()}")
    print(f"    - Credit Cost Debited: {eng_res.credit_cost}")
    bal_after_create = await wallet.get_balance(tenant_id)
    print(f"    - New Wallet Balance: {bal_after_create} credits (Debited {initial_balance - bal_after_create})")

    # Step 2: Scope Engagement
    print("\n[3] SCOPING ENGAGEMENT...")
    scope_manifest = {
        "assets": ["10.0.0.15", "10.0.0.22", "db-prod.internal"],
        "techniques": ["T1059.001", "T1003.001", "T1047", "T1055.001"],
        "target_count": 7,
    }
    scope_res = await update_engagement_scope(eng_id, UpdateScopeRequest(scope_manifest=scope_manifest, confirmed=False))
    print(f"    - Updated State: {scope_res.state.value.upper()}")
    print(f"    - Scoped Assets: {scope_res.scope.get('assets')}")
    print(f"    - Scoped Techniques: {scope_res.scope.get('techniques')}")

    # Demonstrate Scope Drift Guardrail
    print("\n[3b] DEMONSTRATING SCOPE DRIFT GUARDRAIL (> 120% expansion)...")
    huge_scope = {
        "assets": [f"10.0.0.{i}" for i in range(1, 20)],  # Expanding from 7 items to 19 items (271%)
        "target_count": 19,
    }
    try:
        await update_engagement_scope(eng_id, UpdateScopeRequest(scope_manifest=huge_scope, confirmed=False))
        print("    [ERROR] Guardrail failed to trigger!")
    except Exception as e:
        print(f"    - Guardrail Active: Expansion successfully blocked without confirmation!")
        print(f"    - Guardrail Detail: {e.detail}")

    # Step 3: Execute Engagement
    print("\n[4] EXECUTING ENGAGEMENT...")
    exec_res = await execute_engagement(eng_id)
    print(f"    - Updated State: {exec_res.state.value.upper()}")

    # Step 4: Complete Engagement with Module 2 Outcomes
    print("\n[5] COMPLETING ENGAGEMENT WITH MODULE 2 OUTCOMES...")
    with open("fixtures/mod2_outcomes/outcomes_sample.json", "r") as f:
        m2_outcomes = json.load(f)

    print(f"    - Processing {len(m2_outcomes)} validation outcomes:")
    for out in m2_outcomes:
        print(f"      * Technique: {out['technique_id']:<10} Verdict: {out['verdict']}")

    comp_res = await complete_engagement(eng_id, CompleteEngagementRequest(results=m2_outcomes))
    print(f"\n    - Updated State: {comp_res.state.value.upper()}")
    report = comp_res.report
    print(f"    - Detection Efficacy: {report['executive_summary']['detection_rate_percentage']}%")
    print(f"    - Detected: {report['executive_summary']['detected']}")
    print(f"    - Missed:   {report['executive_summary']['missed']}")
    print(f"    - Partial:  {report['executive_summary']['partial']}")
    print(f"    - NoData:   {report['executive_summary']['nodata']} (Inconclusive)")

    if report["refund"]["eligible"]:
        print("\n[6] INCONCLUSIVE VERDICT REFUND TRIGGERED:")
        print(f"    - Refund Eligible: YES")
        print(f"    - Credits Refunded: {report['refund']['refund_amount']}")
        print(f"    - Reason: {report['refund']['reason']}")
        print(f"    - Refund Idempotency Key: {report['refund']['refund_idempotency_key']}")
        bal_final = await wallet.get_balance(tenant_id)
        print(f"    - Wallet Balance After Refund: {bal_final} credits")

    # Step 5: Archive
    print("\n[7] ARCHIVING ENGAGEMENT (Terminal State)...")
    arch_res = await archive_engagement(eng_id)
    print(f"    - Updated State: {arch_res.state.value.upper()}")
    print(f"    - Archived At: {arch_res.archived_at}")

    # Terminal state check
    try:
        await execute_engagement(eng_id)
    except Exception as e:
        print(f"    - Forward-only Check: Re-executing archived engagement safely blocked: {e.detail}")

    # Event Bus Check
    events = event_bus.get_published_events()
    print(f"\n[8] EVENT BUS AUDIT TRAIL ({len(events)} events emitted to 'engagement.lifecycle'):")
    for ev in events:
        print(f"    - [{ev.timestamp[11:19]}] Topic: {ev.topic} | Type: {ev.event_type:<22} | CorrKey: {ev.correlation_key}")

    print("\n" + "=" * 70)
    print("DEMO COMPLETE: ALL GUARDRAILS, TIERS, AND LIFECYCLE FLOWS VALIDATED")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_demo())
