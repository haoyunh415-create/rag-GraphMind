# Support Operations Demo Document

## Bot handoff rule

If the self-service bot fails to solve the same user question twice, the system should create a support ticket.

The support ticket must attach the conversation context, related order id, matched knowledge-base citations, and the last bot answer.

After the ticket is created, the case should be handed to a human agent.

## Operations metrics

The platform should track issue hit rate, answer adoption rate, human handoff rate, average response time, first response time, and customer satisfaction.

The operations dashboard should separate bot-only solved cases from human-assisted solved cases.

The weekly review should include the top unresolved questions and the knowledge documents that produced low-confidence answers.

## Service level

High-priority tickets should receive a first human response within 15 minutes.

Normal-priority tickets should receive a first human response within 2 business hours.

Resolved tickets should keep the full audit trail for later quality review.
