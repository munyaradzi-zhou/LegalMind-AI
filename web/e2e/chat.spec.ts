import { expect, test, type Page } from "@playwright/test";

// The built site with the API mocked in the browser. Sign-in is off here (CI can't sign in to
// Entra ID); the deployed site is checked by hand with a real account.

const API = "http://api.test";

function sse(events: [string, object][]): string {
  return events.map(([event, data]) => `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`).join("");
}

const ANSWER = "Volgens artikel 7:271 BW kan een huur voor onbepaalde tijd worden opgezegd [1]. De opzegtermijn volgt uit de overeenkomst [2].";

async function mockApi(page: Page, me: object) {
  await page.route("**/config.json", (route) => route.fulfill({ json: { apiUrl: API, auth: null } }));
  await page.route(`${API}/api/me`, (route) => route.fulfill({ json: me }));
  await page.route(`${API}/api/chat`, async (route) => {
    const body = route.request().postDataJSON();
    expect(body.stream).toBe(true);
    await route.fulfill({
      headers: { "content-type": "text/event-stream", "access-control-allow-origin": "*" },
      body: sse([
        ["status", { step: "checking_question" }],
        ["status", { step: "searching" }],
        ["status", { step: "answering" }],
        ["status", { step: "checking_answer" }],
        ...ANSWER.split(/(?<= )/).map((piece): [string, object] => ["message", { content: piece, done: false }]),
        [
          "message",
          {
            content: "",
            done: true,
            session_id: "s1",
            agent_name: "legal_research",
            grounding: "documents",
            groundedness_score: 0.95,
            citations: [
              {
                index: 1,
                document_id: "BWBR0005290 art. 271",
                document_title: "Artikel 7:271 BW",
                chunk_content: "1. In afwijking van artikel 228 lid 1 eindigt een voor bepaalde tijd aangegane huur niet ...",
                page_number: null,
                relevance_score: 1,
                url: "https://wetten.overheid.nl/jci1.0:c:BWBR0005290&artikel=271",
              },
              {
                index: 2,
                document_id: "huurovereenkomst-de-wit",
                document_title: "Huurovereenkomst De Wit",
                chunk_content: "Artikel 5. Opzegging geschiedt met inachtneming van een termijn van één maand.",
                page_number: 2,
                relevance_score: 0.91,
              },
            ],
          },
        ],
      ]),
    });
  });
}

const LAWYER = {
  user_id: "anna",
  name: "Anna",
  roles: ["Lawyer"],
  matters: ["BM/2026/H-0412"],
  can_ingest: false,
  sees_privileged: true,
  sign_in_required: false,
};

test("ask a question and open a cited source", async ({ page }) => {
  await mockApi(page, LAWYER);
  await page.goto("/");

  await page.getByLabel("Your question").fill("Hoe zeg ik een huurovereenkomst op?");
  await page.getByRole("button", { name: "Ask" }).click();

  const answer = page.getByTestId("answer");
  await expect(answer).toContainText("kan een huur voor onbepaalde tijd worden opgezegd");
  await expect(page.getByTestId("grounding")).toContainText("Checked against the sources");

  // A live source: the article's text and a link to the official version
  await answer.getByRole("button", { name: "Source 1: Artikel 7:271 BW" }).click();
  const panel = page.getByRole("dialog", { name: "Source 1" });
  await expect(panel).toContainText("In afwijking van artikel 228 lid 1");
  await expect(panel.getByRole("link")).toHaveAttribute("href", /wetten\.overheid\.nl/);
  await panel.getByRole("button", { name: "Close" }).click();

  // A document passage: its page
  await page.getByRole("button", { name: "[2] Huurovereenkomst De Wit" }).click();
  await expect(page.getByRole("dialog", { name: "Source 2" })).toContainText("Page 2");
});

test("only an Admin is offered the upload page", async ({ page }) => {
  await mockApi(page, LAWYER);
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Chat" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Upload" })).toHaveCount(0);

  await mockApi(page, { ...LAWYER, roles: ["Admin"], can_ingest: true });
  await page.reload();
  await expect(page.getByRole("link", { name: "Upload" })).toBeVisible();
});
