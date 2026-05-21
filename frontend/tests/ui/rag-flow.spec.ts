import { expect, test } from "@playwright/test";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const apiBase = process.env.UI_E2E_API_URL || "http://127.0.0.1:8101";

test("user can upload, ask a grounded question, see citations, and inspect trace", async ({ page, request }) => {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "rag-ui-e2e-"));
  const filePath = path.join(tempDir, `ui-e2e-${Date.now()}.txt`);
  const fileName = path.basename(filePath);
  await fs.writeFile(
    filePath,
    [
      "UI E2E verification document.",
      "The beta interface validates browser upload, knowledge listing, grounded answers, citations, and trace inspection.",
      "The recommended user flow is upload a document, ask a grounded question, inspect citations, and open the Trace panel.",
    ].join("\n"),
    "utf8",
  );

  let documentId: string | null = null;
  try {
    await page.goto("/");
    await expect(page.getByTestId("panel-chat")).toBeVisible();

    const stylesLoaded = await page.evaluate(() => {
      const body = window.getComputedStyle(document.body);
      const app = document.querySelector("[data-testid='panel-chat']");
      const appStyle = app ? window.getComputedStyle(app) : null;
      return {
        bodyBackground: body.backgroundColor,
        appDisplay: appStyle?.display || "",
      };
    });
    expect(stylesLoaded.bodyBackground).not.toBe("rgba(0, 0, 0, 0)");
    expect(stylesLoaded.appDisplay).not.toBe("block");

    await page.getByTestId("chat-file-input").setInputFiles(filePath);
    await expect(page.getByText(fileName)).toBeVisible();

    await expect
      .poll(
        async () => {
          const response = await request.get(`${apiBase}/api/kb/documents`);
          if (!response.ok()) return null;
          const payload = await response.json();
          const doc = payload.documents.find((item: any) => item.document_name === fileName);
          if (!doc || !["ready", "partial", "duplicate"].includes(doc.status) || !doc.is_retrievable) {
            return null;
          }
          return doc.document_id || null;
        },
        { timeout: 30_000 },
      )
      .not.toBeNull();

    const docsResponse = await request.get(`${apiBase}/api/kb/documents`);
    expect(docsResponse.ok()).toBeTruthy();
    const docsPayload = await docsResponse.json();
    const uploadedDoc = docsPayload.documents.find((doc: any) => doc.document_name === fileName);
    expect(uploadedDoc).toBeTruthy();
    expect(uploadedDoc.lifecycle_status).toBe("enabled");
    expect(uploadedDoc.is_retrievable).toBe(true);
    documentId = uploadedDoc.document_id;

    await page.getByTestId("tab-knowledge").click();
    await expect(page.getByTestId("panel-knowledge")).toBeVisible();
    await page.getByTestId("knowledge-refresh").click();
    const documentRow = page.getByTestId("knowledge-document-row").filter({ hasText: fileName });
    await expect(documentRow).toBeVisible();
    const statusSelect = documentRow.getByTestId("knowledge-document-status");
    await expect(statusSelect).toHaveValue("enabled");
    await statusSelect.selectOption("test");
    await expect(statusSelect).toHaveValue("test");
    await statusSelect.selectOption("enabled");
    await expect(statusSelect).toHaveValue("enabled");

    await page.getByTestId("tab-chat").click();
    await page.getByTestId("chat-mode-kb").click();
    await page
      .getByTestId("chat-input")
      .fill("According to the uploaded document, what does the beta interface validate?");
    await page.getByTestId("chat-send").click();

    await expect(page.getByTestId("message-user")).toContainText("beta interface");
    await expect(page.getByTestId("message-assistant")).toContainText(/beta interface|browser upload|citations|trace/i);
    await expect(page.getByTestId("message-citations")).toBeVisible();
    await expect(page.getByTestId("message-citations").getByText(fileName)).toBeVisible();

    await expect
      .poll(
        async () => {
          const response = await request.get(`${apiBase}/api/kb/evaluations?limit=10`);
          if (!response.ok()) return null;
          const payload = await response.json();
          const evaluation = payload.evaluations.find((item: any) =>
            String(item.query).includes("beta interface"),
          );
          return evaluation?.overall_score ?? null;
        },
        { timeout: 30_000 },
      )
      .not.toBeNull();

    await page.getByTestId("tab-trace").click();
    await expect(page.getByTestId("panel-trace")).toBeVisible();
    await expect(page.getByTestId("trace-card")).toBeVisible();
    await expect(page.getByTestId("trace-card")).toContainText("beta interface");
    await expect(page.getByTestId("trace-card")).toContainText("质量");
    await expect(page.getByTestId("trace-card")).toContainText("引用裁剪");
    await expect(page.getByTestId("trace-quality-card")).toBeVisible();
    await expect(page.getByTestId("trace-quality-overall")).toContainText(/%/);
    await expect(page.getByTestId("trace-quality-metric")).toHaveCount(4);

    await page.getByTestId("tab-knowledge").click();
    await expect(page.getByTestId("knowledge-quality-summary")).toBeVisible();
    await page.getByTestId("knowledge-refresh").click();
    await expect(page.getByTestId("knowledge-quality-average")).toContainText(/%/);
    await expect(page.getByTestId("knowledge-quality-row").filter({ hasText: "beta interface" })).toBeVisible();
  } finally {
    if (documentId) {
      await request.delete(`${apiBase}/api/documents/${documentId}`);
    }
    await fs.rm(tempDir, { force: true, recursive: true });
  }
});
