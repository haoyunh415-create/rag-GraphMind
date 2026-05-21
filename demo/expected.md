# Demo Expected Results

这份文件不是严格断言脚本，而是面试演示时的对照卡。回答 wording 可以不同，但引用和事实应基本一致。

## 1. Food returns

Question: `Do food items support seven-day no-reason returns?`

Expected answer: Food items do not support seven-day no-reason returns.

Expected citation: `01-commerce-policy.md`, Return policy section.

Trace observation: retrieval should include commerce policy; citations should not rely on the operations or graph document.

## 2. Electronic invoice timing

Question: `How soon are electronic invoices usually sent?`

Expected answer: Electronic invoices are usually sent to the user's mailbox within 24 hours.

Expected citation: `01-commerce-policy.md`, Invoice process section.

Trace observation: citation pruning should keep the invoice-specific chunk.

## 3. Stale shipment tracking

Question: `If shipment tracking is not updated for 48 hours, what should customer service do?`

Expected answer: Customer service should contact the carrier and notify the user.

Expected citation: `01-commerce-policy.md`, Shipping tracking section.

Trace observation: this is a simple retrieval case; performance warnings should normally be empty.

## 4. Bot handoff workflow

Question: `If the bot fails to solve the same user question twice, what should happen next?`

Expected answer: The system should create a support ticket, attach context, and hand the case to a human agent.

Expected citation: `02-support-operations.md`, Bot handoff rule section.

Trace observation: answer should mention both the ticket and human handoff, not only one of them.

## 5. Operations metrics

Question: `Which operations metrics should the platform track?`

Expected answer should include: issue hit rate, answer adoption rate, human handoff rate, average response time, first response time, and customer satisfaction.

Expected citation: `02-support-operations.md`, Operations metrics section.

Trace observation: this is a list answer; citation coverage matters more than style.

## 6. Knowledge Base owner team lead

Question: `Who leads the team that maintains the StarBridge Knowledge Base module?`

Expected answer: Lin Chen.

Expected evidence path: StarBridge Knowledge Base module -> Aurora Search Team -> Lin Chen.

Expected citation: `03-graph-relations.md`, Team ownership section.

Trace observation: graph path evidence is ideal here. If graph services are unavailable, vector retrieval may still answer from the same document.

## 7. Product owner headquarters

Question: `Where is the owner of the StarBridge Support Platform headquartered?`

Expected answer: Hangzhou.

Expected evidence path: StarBridge Support Platform -> Yunlan Technology -> Hangzhou.

Expected citation: `03-graph-relations.md`, Company and product relationships section.

Trace observation: this is the cleanest multi-hop demo question.

## 8. Incident dependency

Question: `Incident K-17 affected which database through the Search API dependency?`

Expected answer: Atlas Vector Database.

Expected evidence path: Incident K-17 -> Search API service -> Atlas Vector Database.

Expected citation: `03-graph-relations.md`, Incident dependency relationships section.

Trace observation: useful for explaining why graph/path evidence is more interpretable than a single retrieved paragraph.
