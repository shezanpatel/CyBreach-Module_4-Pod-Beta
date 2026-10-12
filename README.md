# CyBreach Module 4: Arena - Pod Beta

## Engagement Manager and Lifecycle Management

### 1. Overview
Pod Beta delivers the Engagement Manager for CyBreach Module 4 (Arena). It is responsible for the full lifecycle of engagement sessions, enforcing strict forward-only state progression, launch tiers, credit debits, guardrails, and summary reporting.

### 2. Cross-Pod Integration and Guardrails
- **Idempotency Key Format:** Every debit call to `/wallet/debit` uses the required format `mod4:{engagement_id}`. If the wallet returns HTTP 402, engagement creation is aborted immediately.
- **Event Bus Topics (Kafka / Redpanda):**
  - Published: `engagement.lifecycle` and `campaign.updated`
  - Consumed: `mod2.outcome` and `mod1.playbook`
  - Correlation Key: All emitted events contain a traceable correlation key.
- **Zero Interdependency:** Pod Beta operates independently during development using `MockWalletClient` and static JSON fixtures in `fixtures/`.
- **Code Rule:** Zero em dashes (Unicode U+2014) in code, comments, or documentation. Only hyphens (-) or colons (:) are used.

### 3. Core Capabilities
1. **5-State Machine:**
   - Sequential progression: `CREATED -> SCOPED -> EXECUTING -> COMPLETED -> ARCHIVED`.
   - Forward-only: No restart or undo is allowed under any condition.
   - Enforces business rules: A tenant cannot execute an engagement before scoping it, and cannot complete an engagement before executing it. Terminal state `ARCHIVED` cannot transition to any other state.
2. **5 Launch Tiers:**
   - Quick Scan: 5 credits
   - Guided Assessment: 25 credits
   - Compliance Audit: 100 credits
   - Continuous Validation: subscription (0 direct credits debited)
   - Competitive Arena: free (0 credits)
3. **Guardrails:**
   - **Cooldown Checker:** Prevents rapid cycling through campaigns of the same type per tenant.
   - **Scope Drift Detection:** Detects if an updated scope expands beyond 120% of the original scope. If drift exceeds 120%, explicit confirmation is required.
   - **Budget Validator:** Enforces balance sufficiency prior to debit.
4. **Summary Report Generation:**
   - Compiles Module 2 outcomes across four verdicts: `Detected`, `Missed`, `Partial`, and `NoData`.
   - Flags inconclusive `NoData` outcomes for credit refund eligibility (pro-rata refund, 24-hour SLA deadline, idempotency key `mod4:{engagement_id}:refund`).
   - Automatically generates prioritized remediation recommendations for missed and partial detections.
   - Produces comparative posture deltas against previous engagements.
5. **REST API Endpoints:**
   - `POST /engagement/create`: Validates cooldown, debits wallet with `mod4:{engagement_id}`, initializes in `CREATED` state, emits `engagement.lifecycle`.
   - `PATCH /engagement/{id}/scope`: Checks scope drift (> 120% requires confirmation), advances state to `SCOPED`, emits `engagement.lifecycle`.
   - `POST /engagement/{id}/execute`: Validates state is `SCOPED`, advances to `EXECUTING`, emits `engagement.lifecycle`.
   - `POST /engagement/{id}/complete`: Validates state is `EXECUTING`, compiles Module 2 outcomes into summary report, triggers automated refund for `NoData`, advances to `COMPLETED`, emits `engagement.lifecycle`.
   - `POST /engagement/{id}/archive`: Validates state is `COMPLETED`, advances to terminal `ARCHIVED` state, emits `engagement.lifecycle`.

### 4. Running Tests
```bash
python -m pytest -v
```
All 55 unit and integration tests verify state transitions, launch tiers, guardrails, report generation, API endpoints, and zero em dash compliance.
