"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const outreach = require("../../apps_script/tender_outreach/Code.js");

function campaign(overrides = {}) {
  return {
    id: "tender-intro-v1",
    status: "тест",
    dailyLimit: 20,
    runLimit: 5,
    sendWindow: "10:00–17:00",
    days: "пн–пт",
    approved: true,
    approvedAt: "2026-08-24",
    comment: "TEST_DRAFTS_APPROVED",
    ...overrides,
  };
}

function workingCampaign(overrides = {}) {
  return campaign({
    status: "одобрена",
    comment: "WORK_DRAFTS_APPROVED",
    ...overrides,
  });
}

function productionCampaign(overrides = {}) {
  return workingCampaign({
    comment: "WORK_DRAFTS_APPROVED; PRODUCTION_SEND_APPROVED",
    ...overrides,
  });
}

function automatedCampaign(overrides = {}) {
  return productionCampaign({
    comment:
      "WORK_DRAFTS_APPROVED; PRODUCTION_SEND_APPROVED; " +
      "PUBLIC_TENDER_OUTREACH_AUTHORIZED",
    ...overrides,
  });
}

function candidate(overrides = {}) {
  return {
    candidateId: "candidate-1",
    campaignId: "tender-intro-v1",
    email: "sales@example.ru",
    organization: "ООО Пример",
    region: "Республика Крым",
    contactPerson: "",
    contactSource: "https://zakupki.gov.ru/organization/1",
    sourceTender: "https://zakupki.gov.ru/tender/1",
    decision: "ready_for_campaign_review",
    decisionReason: "",
    mailingStatus: "в очереди",
    sentAt: "",
    stage: "",
    draftId: "",
    messageId: "",
    threadId: "",
    approved: true,
    autoSend: false,
    contactBasis: "согласие получено",
    consentStatus: "подтверждено",
    consentEvidence: "Входящее письмо от 2026-08-24",
    consentDate: "2026-08-24",
    ...overrides,
  };
}

const template = { subject: "Предложение для {{Организация}}", body: "Добрый день" };

test("test draft eligibility requires every approval gate", () => {
  assert.equal(outreach.candidateEligibility(candidate(), campaign(), {}, template), "");
  assert.equal(
    outreach.candidateEligibility(candidate({ approved: false }), campaign(), {}, template),
    "candidate_not_approved"
  );
  assert.equal(
    outreach.candidateEligibility(candidate({ autoSend: true }), campaign(), {}, template),
    "auto_send_must_remain_false_in_test"
  );
  assert.equal(
    outreach.candidateEligibility(candidate(), campaign({ approved: false }), {}, template),
    "campaign_not_approved"
  );
});

test("blocked template and global stop-list always win", () => {
  assert.equal(
    outreach.candidateEligibility(candidate(), campaign(), {}, {
      subject: "[ЗАБЛОКИРОВАНО] Нет текста",
      body: "Текст",
    }),
    "template_blocked"
  );
  assert.equal(
    outreach.candidateEligibility(
      candidate(),
      campaign(),
      { "sales@example.ru": true },
      template
    ),
    "globally_suppressed"
  );
});

test("public contact alone never passes the consent gate", () => {
  assert.equal(
    outreach.candidateEligibility(
      candidate({ consentStatus: "неизвестно" }),
      campaign(),
      {},
      template
    ),
    "consent_not_confirmed"
  );
  assert.equal(
    outreach.candidateEligibility(
      candidate({ contactBasis: "не установлено" }),
      campaign(),
      {},
      template
    ),
    "contact_basis_not_permitted"
  );
  assert.equal(
    outreach.candidateEligibility(
      candidate({ consentEvidence: "" }),
      campaign(),
      {},
      template
    ),
    "consent_evidence_missing"
  );
});

test("working drafts require a separately approved campaign", () => {
  assert.equal(
    outreach.workingDraftEligibility(candidate(), workingCampaign(), {}, template),
    ""
  );
  assert.equal(
    outreach.workingDraftEligibility(candidate(), campaign(), {}, template),
    "campaign_not_approved_for_working_drafts"
  );
  assert.equal(
    outreach.workingDraftEligibility(
      candidate(),
      workingCampaign({ comment: "TEST_DRAFTS_APPROVED" }),
      {},
      template
    ),
    "campaign_working_draft_approval_phrase_missing"
  );
});

test("working drafts remain row-approved, consent-gated and deduplicated", () => {
  assert.equal(
    outreach.workingDraftEligibility(
      candidate({ approved: false }),
      workingCampaign(),
      {},
      template
    ),
    "candidate_not_approved"
  );
  assert.equal(
    outreach.workingDraftEligibility(
      candidate({ consentStatus: "неизвестно" }),
      workingCampaign(),
      {},
      template
    ),
    "consent_not_confirmed"
  );
  assert.equal(
    outreach.workingDraftEligibility(
      candidate(),
      workingCampaign(),
      {},
      template,
      { "sales@example.ru": true }
    ),
    "email_already_has_draft"
  );
});

test("working draft batch has a conservative hard cap", () => {
  assert.equal(outreach.CONFIG.maxHardWorkingDraftsPerRun, 20);
});

test("automated preparation requires explicit campaign authorization and tender evidence", () => {
  const publicCandidate = candidate({
    decision: "needs_contact_review",
    decisionReason: "contact_not_marked_ready",
    mailingStatus: "заблокировано",
    approved: false,
    consentStatus: "неизвестно",
    contactBasis: "не установлено",
    consentEvidence: "",
    consentDate: "",
  });
  assert.equal(
    outreach.automatedDraftEligibility(
      publicCandidate,
      automatedCampaign(),
      {},
      template,
      {}
    ),
    ""
  );
  assert.equal(
    outreach.automatedDraftEligibility(
      publicCandidate,
      productionCampaign(),
      {},
      template,
      {}
    ),
    "public_tender_outreach_not_authorized"
  );
  assert.equal(
    outreach.automatedDraftEligibility(
      { ...publicCandidate, sourceTender: "" },
      automatedCampaign(),
      {},
      template,
      {}
    ),
    "public_tender_evidence_missing"
  );
});

test("production permits a reviewed public tender campaign without rewriting consent fields", () => {
  const ready = candidate({
    mailingStatus: "рабочий черновик",
    stage: "рабочий черновик",
    draftId: "draft-public-1",
    autoSend: true,
    contactBasis: "не установлено",
    consentStatus: "неизвестно",
    consentEvidence: "",
    consentDate: "",
  });
  assert.equal(
    outreach.productionSendEligibility(ready, automatedCampaign(), {}, template),
    ""
  );
});

test("production send requires its own campaign phrase and explicit row approval", () => {
  const ready = candidate({
    mailingStatus: "рабочий черновик",
    stage: "рабочий черновик",
    draftId: "draft-1",
    autoSend: true,
  });
  assert.equal(
    outreach.productionSendEligibility(ready, productionCampaign(), {}, template),
    ""
  );
  assert.equal(
    outreach.productionSendEligibility(ready, workingCampaign(), {}, template),
    "campaign_production_approval_phrase_missing"
  );
  assert.equal(
    outreach.productionSendEligibility(
      { ...ready, autoSend: false },
      productionCampaign(),
      {},
      template
    ),
    "auto_send_not_approved"
  );
});

test("production send cannot reuse a sent row or a suppressed email", () => {
  const ready = candidate({
    mailingStatus: "рабочий черновик",
    stage: "рабочий черновик",
    draftId: "draft-1",
    autoSend: true,
  });
  assert.equal(
    outreach.productionSendEligibility(
      { ...ready, messageId: "message-1" },
      productionCampaign(),
      {},
      template
    ),
    "already_sent"
  );
  assert.equal(
    outreach.productionSendEligibility(
      ready,
      productionCampaign(),
      { "sales@example.ru": true },
      template
    ),
    "globally_suppressed"
  );
});

test("production batch and schedule have conservative hard limits", () => {
  assert.equal(outreach.clampProductionBatchLimit("50"), 10);
  assert.equal(outreach.clampProductionBatchLimit("0"), 5);
  assert.equal(outreach.CONFIG.maxHardSendsPerDay, 50);
  assert.equal(outreach.isScheduleOpen(productionCampaign(), 1, 10 * 60), true);
  assert.equal(outreach.isScheduleOpen(productionCampaign(), 5, 17 * 60), true);
  assert.equal(outreach.isScheduleOpen(productionCampaign(), 6, 12 * 60), false);
  assert.equal(outreach.isScheduleOpen(productionCampaign(), 3, 9 * 60 + 59), false);
});

test("template rendering is deterministic", () => {
  assert.equal(
    outreach.renderTemplate("{{Организация}} — {{Регион}}", candidate()),
    "ООО Пример — Республика Крым"
  );
});

test("HTML version contains active links and preserves line breaks", () => {
  const html = outreach.plainTextToHtml(
    "Каталог: https://climat-simf.ru\nКлимат: https://splithome.ru\nКомпания: https://simfer.com.ru"
  );
  assert.match(html, /href="https:\/\/climat-simf\.ru"/);
  assert.match(html, /href="https:\/\/splithome\.ru"/);
  assert.match(html, /href="https:\/\/simfer\.com\.ru"/);
  assert.match(html, /<br>/);
});

test("HTML version emphasizes company identity and reply call to action", () => {
  const html = outreach.plainTextToHtml(
    "Просим рассмотреть ООО «Технолайн Трейд».\n" +
      "Направьте нам запрос, техническое задание или спецификацию ответным письмом — ответим оперативно."
  );
  assert.match(html, /<strong>ООО «Технолайн Трейд»<\/strong>/);
  assert.match(
    html,
    /<strong>Направьте нам запрос, техническое задание или спецификацию ответным письмом<\/strong>/
  );
});

test("responsive visual HTML contains CID image, mobile layout and working calls to action", () => {
  const html = outreach.buildResponsiveHtml(
    "Добрый день!\n\nУважаемые коллеги!\n\n" +
      "Направьте нам запрос, техническое задание или спецификацию ответным письмом.\n\n" +
      "Каталог техники: https://simfer.com.ru",
    {
      from: "alexey.gurinenko@simfer.com.ru",
      replyTo: "alexey.gurinenko@simfer.com.ru",
      heroContentId: "tenderOutreachHero",
    }
  );
  assert.match(html, /cid:tenderOutreachHero/);
  assert.match(html, /@media screen and \(max-width:600px\)/);
  assert.match(html, /Направить спецификацию/);
  assert.match(html, /href="https:\/\/simfer\.com\.ru"/);
  assert.match(html, /subject=%D0%9D%D0%B5%20%D0%BF%D0%B8%D1%81%D0%B0%D1%82%D1%8C/);
});

test("mail headers cannot be extended through template values", () => {
  assert.equal(outreach.sanitizeHeader("Тема\r\nBcc: attacker@example.com"), "Тема Bcc: attacker@example.com");
});

test("mailbox signal helpers recognize bounces and explicit opt-outs", () => {
  assert.deepEqual(
    outreach.extractEmailsFromText(
      "Final-Recipient: rfc822; SALES@Example.ru\nFrom: Mailer-Daemon@example.net"
    ),
    { "sales@example.ru": true, "mailer-daemon@example.net": true }
  );
  assert.equal(outreach.isOptOutText("Прошу больше не писать."), true);
  assert.equal(outreach.isOptOutText("Спасибо, напишите завтра"), false);
});

test("audit event row follows the sheet header order", () => {
  const timestamp = new Date("2026-08-24T20:29:28Z");
  assert.deepEqual(
    outreach.buildEventRow(
      timestamp,
      "event-1",
      "tender-intro-v1",
      "candidate-1",
      "email-hash",
      "test_draft_created",
      "в очереди",
      "тестовый черновик",
      "owner@example.com",
      "control-only"
    ),
    [
      timestamp,
      "event-1",
      "tender-intro-v1",
      "candidate-1",
      "email-hash",
      "test_draft_created",
      "в очереди",
      "тестовый черновик",
      "owner@example.com",
      "control-only",
    ]
  );
});
