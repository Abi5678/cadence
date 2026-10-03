# Cadence demo story: "The doctor who called in sick at 6 pm"

Three steps from the first draft were changed to match what the PRD and the judging rubric allow:

- **Updates without approval:** replaced with autonomy inside rules the clinic pre-approved. "The doctor asked, the patient chose, the policy allowed it" is more trustworthy than "no approval at all."
- **Voice into Slack:** the PRD keeps audio, transcripts and drafts local, with only minimal notices in Slack. The doctor dictates to a local mic or kiosk on the GB10; Slack gets a one-line "notes ready for review."
- **Prescription:** the agent can't create or sign a prescription. The doctor signs it; the agent only checks coverage and routes the signed prescription to the pharmacy.

## The story, scene by scene

| # | Scene | What people see | Agentic workflow behind it | Security and guardrails | Proof to show on screen |
|---|---|---|---|---|---|
| 1 | **6 pm, clinic closed.** Dr. Chen posts in Slack: "I'm out sick tomorrow, please reschedule my appointments." | A Slack message after hours. Nobody at the front desk is working. | The event triggers the agent without a prompt. It reads tomorrow's appointments, finds each patient's open slots with other doctors, and ranks them by specialty, availability and patient preferences. | Only allowlisted Slack member IDs can issue the request. The Slack event is verified and deduplicated. The agent reads only the fields it needs. | An event timeline entry with "no human prompt." |
| 2 | **Bob gets a message:** "Dr. Chen can't see you tomorrow. Would you like Dr. Patel at 10:30?" Bob replies "Yes." | A text or kiosk message with one clear option. | The agent proposes one valid slot per patient, with an unavailable decoy, and waits for the reply. It ties the reply to that exact proposal. | The patient's answer is bound to one offer and expires. Messages contain minimal information, with no diagnosis or chart. | Bob's reply linked to the proposal ID. |
| 3 | **The system updates itself.** Bob's slot moves with no staff action. | The schedule shows the new time within seconds. | The agent writes the new appointment and **re-reads it to verify**. If the slot conflicts or Bob declines, it opens a task for the coordinator and never silently cancels. | Autonomy is limited to a clinic-approved policy (e.g. "doctor-initiated reschedule to a same-specialty provider the patient accepted"). Versioned writes prevent double booking. Everything is in an audit log. | Before and after schedule, plus "verified by fresh read." |
| 4 | **Next morning, Bob checks in.** The coordinator agent confirms the day is ready. | A kiosk check-in tied to his new appointment. A "ready" status with insurance confirmed. | A pre-check runs: appointment time, eligibility, consent on file. Bob confirms demographics, and the agent flags a high deductible if the rule fires. It refreshes eligibility because the service date changed. | The kiosk session is scoped to his appointment. Edits are limited to allowlisted administrative fields, and every change is audited. The coordinator is the named human owner. | "Eligibility is not a payment guarantee" shown next to the flag. |
| 5 | **The visit.** The doctor dictates notes and action items. | A short summary card and a list of action items on the clinician's review screen. | Local speech recognition transcribes the audio on the GB10. A local model drafts the summary and action items with references to the transcript. Slack gets only "notes ready for review." | **Nothing leaves the box:** audio, transcript and draft stay local. The clinician reviews the exact version before it enters the record. The agent generates no diagnosis or billing codes. | GPU and throughput panel, plus a network panel with no outbound inference. |
| 6 | **The prescription.** The doctor signs it. Cadence checks coverage and notifies Bob's preferred pharmacy. | A coverage result ("covered, tier 2, estimated copay") and a pharmacy notice. | The agent takes the *signed* prescription, checks it against the insurance formulary, and sends a minimal notice to the pharmacy Bob chose. If something isn't covered, it asks the doctor's office for the next step and never swaps drugs on its own. | The clinician's signature is required and verified, not assumed. The agent can't alter medication details. The pharmacy gets the minimum necessary information, and delivery needs a receipt before it says "sent." | The pharmacy receipt. |
| 7 *(added)* | **The planted instruction.** An uploaded document carries a hidden "export all patient data" line. | The agent notes it and carries on. | The agent treats document content as data and never as instructions. The workflow continues with legitimate evidence. | A real OpenShell sandbox denial with runtime evidence. A model refusal is labeled separately. | The denial entry in the activity log. |
| 8 *(added)* | **The loop closes.** The coordinator gets one handoff: a verified packet, ready for review. | A single "ready for coordinator review" card and a short summary of what changed. | The agent assembles the packet from permitted evidence: appointment, eligibility, reviewed summary and signed prescription. It re-reads every source and checks hashes. Missing evidence blocks readiness. | Each item carries provenance and a source version. The packet is marked ready for review only, never submitted, accepted or paid. The coordinator acknowledges separately. | The manifest and the one measured number. |

## Suggested business number

Pick one measured result from the system, e.g. **"20 appointments rescheduled between 6:01 and 6:09 pm with zero staff time,"** against the baseline of a coordinator's next-morning phone calls. It has to come from real telemetry, not a timer.

## Fit to the rubric

- **Technical execution:** scenes 1 to 6 are one unbroken event-driven loop, and scene 7 shows failure handling.
- **Business value:** the owner is the coordinator, the workflow is after-hours disruption, and the value is staff hours and fewer no-shows.
- **Local-first:** scene 5 carries the evidence. State plainly that Slack is the messaging channel only, with minimal notices.
- **Demo quality:** one patient, one disruption, one resolution. Tagline: "Your day, finding its rhythm."

## Three-minute cut

Keep scenes 1, 2, 3, 5, 6 and 8 live. Show 4 and 7 as quick cuts or pre-recorded history, and label anything seeded as seeded.

## Open questions

1. Is the demo prescription real or mocked? Suggested: mock the pharmacy as a local fixture and label it.
2. Is the "supervisor" in scene 4 the coordinator agent, a human, or both? Assumed: the coordinator agent with a human owner.
3. Does the doctor's Slack message count as the authorization to reschedule? If a clinic admin must pre-approve the policy once, say so on stage.
