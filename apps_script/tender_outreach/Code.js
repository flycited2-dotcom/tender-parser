/*
 * Tender outreach mailer for Google Apps Script.
 *
 * Test drafts, working drafts and production sends use separate explicit
 * switches. Every real recipient is rechecked against campaign approval,
 * per-row approval, consent evidence, the global stop-list and daily limits.
 */

var TenderOutreach = (function () {
  "use strict";

  var CONFIG = {
    spreadsheetId: "1Dos070wEj_zwymVn46i4mcC4fWMLpDsZVmC7KkX8HSQ",
    sheets: {
      queue: "Очередь",
      template: "Шаблон",
      campaigns: "Кампании",
      stoplist: "Стоп-лист",
      events: "События",
    },
    properties: {
      testMode: "TENDER_OUTREACH_TEST_MODE",
      workingDraftsMode: "TENDER_OUTREACH_WORKING_DRAFTS_MODE",
      productionMode: "TENDER_OUTREACH_PRODUCTION_MODE",
      schedulerMode: "TENDER_OUTREACH_SCHEDULER_MODE",
      productionBatchLimit: "TENDER_OUTREACH_BATCH_LIMIT",
      testRecipient: "TENDER_OUTREACH_TEST_RECIPIENT",
      senderAlias: "TENDER_OUTREACH_FROM_ALIAS",
      senderName: "TENDER_OUTREACH_SENDER_NAME",
      replyTo: "TENDER_OUTREACH_REPLY_TO",
    },
    queue: {
      decisionReady: "ready_for_campaign_review",
      statusQueued: "в очереди",
      statusTestDraftCreating: "создание тестового черновика",
      statusTestDraft: "тестовый черновик",
      statusTestDraftError: "ошибка тестового черновика",
      statusWorkingDraftCreating: "создание рабочего черновика",
      statusWorkingDraft: "рабочий черновик",
      statusWorkingDraftError: "ошибка рабочего черновика",
      stageWorkingDraft: "рабочий черновик",
      statusSending: "отправляется",
      statusSent: "отправлено",
      statusSendError: "ошибка отправки",
      stageSent: "отправлено",
    },
    campaign: {
      testStatus: "тест",
      approvalPhrase: "TEST_DRAFTS_APPROVED",
      workingDraftStatus: "одобрена",
      workingDraftApprovalPhrase: "WORK_DRAFTS_APPROVED",
      productionStatus: "одобрена",
      productionApprovalPhrase: "PRODUCTION_SEND_APPROVED",
    },
    blockedSubjectPrefixes: ["[ЗАБЛОКИРОВАНО]", "[ЧЕРНОВИК"],
    maxHardTestDraftsPerRun: 5,
    maxHardWorkingDraftsPerRun: 10,
    maxHardSendsPerRun: 10,
    maxHardSendsPerDay: 50,
    defaultProductionBatchLimit: 5,
  };

  var QUEUE_HEADERS = {
    candidateId: "ID кандидата",
    campaignId: "ID кампании",
    email: "Email",
    organization: "Организация",
    region: "Регион",
    contactPerson: "Контактное лицо",
    decision: "Решение",
    mailingStatus: "Статус рассылки",
    sentAt: "Дата отправки",
    stage: "Этап",
    draftId: "ID черновика",
    messageId: "ID сообщения",
    threadId: "ID цепочки",
    note: "Заметка",
    approved: "Одобрено",
    autoSend: "Автоотправка",
    contactBasis: "Основание обращения",
    consentStatus: "Статус согласия",
    consentEvidence: "Подтверждение согласия",
    consentDate: "Дата согласия",
  };

  var CAMPAIGN_HEADERS = {
    id: "ID кампании",
    status: "Статус",
    dailyLimit: "Лимит/день",
    runLimit: "Лимит/запуск",
    sendWindow: "Окно отправки",
    days: "Дни",
    approved: "Одобрено владельцем",
    approvedAt: "Дата одобрения",
    comment: "Комментарий",
  };

  function normalizeEmail(value) {
    var text = String(value || "").trim().toLowerCase();
    var match = text.match(/[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}/i);
    return match ? match[0].replace(/\.$/, "") : "";
  }

  function asBoolean(value) {
    return value === true || String(value || "").trim().toLowerCase() === "true";
  }

  function normalizeLabel(value) {
    return String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
  }

  function indexHeaders(headers) {
    var result = {};
    headers.forEach(function (header, index) {
      var key = String(header || "").trim();
      if (key) result[key] = index;
    });
    return result;
  }

  function requireHeaders(index, required, tableName) {
    Object.keys(required).forEach(function (key) {
      var header = required[key];
      if (typeof index[header] !== "number") {
        throw new Error("В листе «" + tableName + "» нет столбца «" + header + "»");
      }
    });
  }

  function isBlockedTemplate(template) {
    var subject = String((template && template.subject) || "").trim();
    var body = String((template && template.body) || "").trim();
    return (
      !subject ||
      !body ||
      CONFIG.blockedSubjectPrefixes.some(function (prefix) {
        return subject.indexOf(prefix) === 0;
      })
    );
  }

  function renderTemplate(text, candidate) {
    var values = {
      "{{Организация}}": candidate.organization || "",
      "{{Регион}}": candidate.region || "",
      "{{Контактное лицо}}": candidate.contactPerson || "",
      "{{Email}}": candidate.email || "",
    };
    return Object.keys(values).reduce(function (rendered, placeholder) {
      return rendered.split(placeholder).join(String(values[placeholder]));
    }, String(text || ""));
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function plainTextToHtml(text) {
    var escaped = escapeHtml(text);
    var linked = escaped.replace(/https?:\/\/[^\s<]+/g, function (url) {
      return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + url + "</a>";
    });
    var emphasized = linked
      .replace(/ООО «Технолайн Трейд»/g, "<strong>ООО «Технолайн Трейд»</strong>")
      .replace(
        /Направьте нам запрос, техническое задание или спецификацию ответным письмом/g,
        "<strong>Направьте нам запрос, техническое задание или спецификацию ответным письмом</strong>"
      );
    return '<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.5;color:#202124">' +
      emphasized.replace(/\r?\n/g, "<br>") +
      "</div>";
  }

  function sanitizeHeader(value) {
    return String(value || "").replace(/[\r\n]+/g, " ").trim();
  }

  function encodeMimeHeader(value) {
    var safe = sanitizeHeader(value);
    return "=?UTF-8?B?" +
      Utilities.base64Encode(safe, Utilities.Charset.UTF_8) +
      "?=";
  }

  function base64MimeBody(value) {
    var encoded = Utilities.base64Encode(
      String(value || ""),
      Utilities.Charset.UTF_8
    );
    return (encoded.match(/.{1,76}/g) || [""]).join("\r\n");
  }

  function buildRawDraftMessage(to, subject, plainBody, options) {
    var boundary = "tender-outreach-" + Utilities.getUuid();
    var fromEmail = normalizeEmail(options.from);
    var fromHeader = fromEmail;
    if (options.name) fromHeader = encodeMimeHeader(options.name) + " <" + fromEmail + ">";
    var headers = [
      "From: " + fromHeader,
      "To: " + normalizeEmail(to),
      "Subject: " + encodeMimeHeader(subject),
      "MIME-Version: 1.0",
      'Content-Type: multipart/alternative; boundary="' + boundary + '"',
    ];
    if (options.replyTo) headers.splice(2, 0, "Reply-To: " + normalizeEmail(options.replyTo));

    var raw = headers.join("\r\n") + "\r\n\r\n" +
      "--" + boundary + "\r\n" +
      "Content-Type: text/plain; charset=UTF-8\r\n" +
      "Content-Transfer-Encoding: base64\r\n\r\n" +
      base64MimeBody(plainBody) + "\r\n" +
      "--" + boundary + "\r\n" +
      "Content-Type: text/html; charset=UTF-8\r\n" +
      "Content-Transfer-Encoding: base64\r\n\r\n" +
      base64MimeBody(plainTextToHtml(plainBody)) + "\r\n" +
      "--" + boundary + "--";

    return Utilities.base64EncodeWebSafe(raw, Utilities.Charset.UTF_8).replace(/=+$/, "");
  }

  function createDraftViaGmailApi(to, subject, plainBody, options) {
    return Gmail.Users.Drafts.create(
      { message: { raw: buildRawDraftMessage(to, subject, plainBody, options) } },
      "me"
    );
  }

  function hashEmail(email) {
    var normalized = normalizeEmail(email);
    if (!normalized) return "";
    return Utilities.computeDigest(
      Utilities.DigestAlgorithm.SHA_256,
      normalized,
      Utilities.Charset.UTF_8
    ).map(function (byte) {
      var unsigned = byte < 0 ? byte + 256 : byte;
      return ("0" + unsigned.toString(16)).slice(-2);
    }).join("");
  }

  function buildEventRow(
    timestamp,
    eventId,
    campaignId,
    candidateId,
    emailHash,
    type,
    oldStatus,
    newStatus,
    initiator,
    details
  ) {
    return [
      timestamp,
      eventId,
      campaignId,
      candidateId,
      emailHash,
      type,
      oldStatus,
      newStatus,
      initiator,
      details || "",
    ];
  }

  function validateCampaignForTest(campaign) {
    if (!campaign) return "campaign_not_found";
    if (normalizeLabel(campaign.status) !== CONFIG.campaign.testStatus) {
      return "campaign_not_in_test_status";
    }
    if (!asBoolean(campaign.approved)) return "campaign_not_approved";
    if (!String(campaign.approvedAt || "").trim()) return "campaign_approval_date_missing";
    if (String(campaign.comment || "").indexOf(CONFIG.campaign.approvalPhrase) < 0) {
      return "campaign_test_approval_phrase_missing";
    }
    return "";
  }

  function validateCampaignForWorkingDrafts(campaign) {
    if (!campaign) return "campaign_not_found";
    if (normalizeLabel(campaign.status) !== CONFIG.campaign.workingDraftStatus) {
      return "campaign_not_approved_for_working_drafts";
    }
    if (!asBoolean(campaign.approved)) return "campaign_not_approved";
    if (!String(campaign.approvedAt || "").trim()) return "campaign_approval_date_missing";
    if (
      String(campaign.comment || "").indexOf(
        CONFIG.campaign.workingDraftApprovalPhrase
      ) < 0
    ) {
      return "campaign_working_draft_approval_phrase_missing";
    }
    return "";
  }

  function validateCampaignForProduction(campaign) {
    if (!campaign) return "campaign_not_found";
    if (normalizeLabel(campaign.status) !== CONFIG.campaign.productionStatus) {
      return "campaign_not_approved_for_production";
    }
    if (!asBoolean(campaign.approved)) return "campaign_not_approved";
    if (!String(campaign.approvedAt || "").trim()) return "campaign_approval_date_missing";
    if (
      String(campaign.comment || "").indexOf(
        CONFIG.campaign.productionApprovalPhrase
      ) < 0
    ) {
      return "campaign_production_approval_phrase_missing";
    }
    return "";
  }

  function candidateFromRow(row, index) {
    return {
      candidateId: String(row[index[QUEUE_HEADERS.candidateId]] || "").trim(),
      campaignId: String(row[index[QUEUE_HEADERS.campaignId]] || "").trim(),
      email: normalizeEmail(row[index[QUEUE_HEADERS.email]]),
      organization: String(row[index[QUEUE_HEADERS.organization]] || "").trim(),
      region: String(row[index[QUEUE_HEADERS.region]] || "").trim(),
      contactPerson: String(row[index[QUEUE_HEADERS.contactPerson]] || "").trim(),
      decision: String(row[index[QUEUE_HEADERS.decision]] || "").trim(),
      mailingStatus: String(row[index[QUEUE_HEADERS.mailingStatus]] || "").trim(),
      sentAt: row[index[QUEUE_HEADERS.sentAt]],
      stage: String(row[index[QUEUE_HEADERS.stage]] || "").trim(),
      draftId: String(row[index[QUEUE_HEADERS.draftId]] || "").trim(),
      messageId: String(row[index[QUEUE_HEADERS.messageId]] || "").trim(),
      threadId: String(row[index[QUEUE_HEADERS.threadId]] || "").trim(),
      approved: asBoolean(row[index[QUEUE_HEADERS.approved]]),
      autoSend: asBoolean(row[index[QUEUE_HEADERS.autoSend]]),
      contactBasis: normalizeLabel(row[index[QUEUE_HEADERS.contactBasis]]),
      consentStatus: normalizeLabel(row[index[QUEUE_HEADERS.consentStatus]]),
      consentEvidence: String(row[index[QUEUE_HEADERS.consentEvidence]] || "").trim(),
      consentDate: String(row[index[QUEUE_HEADERS.consentDate]] || "").trim(),
    };
  }

  function candidateEligibilityAfterCampaign(
    candidate,
    campaign,
    stopEmails,
    template,
    campaignReason,
    draftedEmails
  ) {
    if (campaignReason) return campaignReason;
    if (isBlockedTemplate(template)) return "template_blocked";
    if (!candidate.candidateId) return "candidate_id_missing";
    if (!candidate.email) return "email_missing_or_invalid";
    if (candidate.campaignId !== campaign.id) return "campaign_mismatch";
    if (candidate.decision !== CONFIG.queue.decisionReady) return "decision_not_ready";
    if (normalizeLabel(candidate.mailingStatus) !== CONFIG.queue.statusQueued) {
      return "mailing_status_not_queued";
    }
    if (!candidate.approved) return "candidate_not_approved";
    if (candidate.autoSend) return "auto_send_must_remain_false_in_test";
    if (candidate.draftId) return "draft_already_exists";
    if (draftedEmails && draftedEmails[candidate.email]) {
      return "email_already_has_draft";
    }
    if (stopEmails && stopEmails[candidate.email]) return "globally_suppressed";
    if (candidate.consentStatus !== "подтверждено") return "consent_not_confirmed";
    if (
      ["согласие получено", "входящий запрос", "действующий договор"].indexOf(
        candidate.contactBasis
      ) < 0
    ) {
      return "contact_basis_not_permitted";
    }
    if (!candidate.consentEvidence) return "consent_evidence_missing";
    if (!candidate.consentDate) return "consent_date_missing";
    return "";
  }

  function candidateEligibility(candidate, campaign, stopEmails, template) {
    return candidateEligibilityAfterCampaign(
      candidate,
      campaign,
      stopEmails,
      template,
      validateCampaignForTest(campaign),
      null
    );
  }

  function workingDraftEligibility(
    candidate,
    campaign,
    stopEmails,
    template,
    draftedEmails
  ) {
    return candidateEligibilityAfterCampaign(
      candidate,
      campaign,
      stopEmails,
      template,
      validateCampaignForWorkingDrafts(campaign),
      draftedEmails || null
    );
  }

  function productionSendEligibility(candidate, campaign, stopEmails, template) {
    var campaignReason = validateCampaignForProduction(campaign);
    if (campaignReason) return campaignReason;
    if (isBlockedTemplate(template)) return "template_blocked";
    if (!candidate.candidateId) return "candidate_id_missing";
    if (!candidate.email) return "email_missing_or_invalid";
    if (candidate.campaignId !== campaign.id) return "campaign_mismatch";
    if (candidate.decision !== CONFIG.queue.decisionReady) return "decision_not_ready";
    if (normalizeLabel(candidate.mailingStatus) !== CONFIG.queue.statusWorkingDraft) {
      return "working_draft_status_required";
    }
    if (normalizeLabel(candidate.stage) !== CONFIG.queue.stageWorkingDraft) {
      return "working_draft_stage_required";
    }
    if (!candidate.approved) return "candidate_not_approved";
    if (!candidate.autoSend) return "auto_send_not_approved";
    if (!candidate.draftId) return "draft_missing";
    if (candidate.messageId || candidate.sentAt) return "already_sent";
    if (stopEmails && stopEmails[candidate.email]) return "globally_suppressed";
    if (candidate.consentStatus !== "подтверждено") return "consent_not_confirmed";
    if (
      ["согласие получено", "входящий запрос", "действующий договор"].indexOf(
        candidate.contactBasis
      ) < 0
    ) {
      return "contact_basis_not_permitted";
    }
    if (!candidate.consentEvidence) return "consent_evidence_missing";
    if (!candidate.consentDate) return "consent_date_missing";
    return "";
  }

  function getSpreadsheet() {
    var spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
    if (!spreadsheet || spreadsheet.getId() !== CONFIG.spreadsheetId) {
      throw new Error("Скрипт запущен не из утверждённой тендерной таблицы");
    }
    return spreadsheet;
  }

  function requireSheet(spreadsheet, name) {
    var sheet = spreadsheet.getSheetByName(name);
    if (!sheet) throw new Error("Не найден обязательный лист «" + name + "»");
    return sheet;
  }

  function readTable(sheet) {
    var lastRow = sheet.getLastRow();
    var lastColumn = sheet.getLastColumn();
    if (lastRow < 1 || lastColumn < 1) return { headers: [], rows: [], index: {} };
    var values = sheet.getRange(1, 1, lastRow, lastColumn).getValues();
    var headers = values[0];
    return { headers: headers, rows: values.slice(1), index: indexHeaders(headers) };
  }

  function readTemplate(sheet) {
    var values = sheet.getRange(1, 1, Math.max(sheet.getLastRow(), 1), 2).getValues();
    var keyed = {};
    values.forEach(function (row) {
      keyed[String(row[0] || "").trim()] = row[1];
    });
    return { subject: keyed["Тема"] || "", body: keyed["Тело"] || "" };
  }

  function readCampaigns(sheet) {
    var table = readTable(sheet);
    requireHeaders(table.index, CAMPAIGN_HEADERS, CONFIG.sheets.campaigns);
    var campaigns = {};
    table.rows.forEach(function (row) {
      var id = String(row[table.index[CAMPAIGN_HEADERS.id]] || "").trim();
      if (!id) return;
      campaigns[id] = {
        id: id,
        status: row[table.index[CAMPAIGN_HEADERS.status]],
        dailyLimit: Number(row[table.index[CAMPAIGN_HEADERS.dailyLimit]]) || 0,
        runLimit: Number(row[table.index[CAMPAIGN_HEADERS.runLimit]]) || 0,
        sendWindow: String(row[table.index[CAMPAIGN_HEADERS.sendWindow]] || "").trim(),
        days: String(row[table.index[CAMPAIGN_HEADERS.days]] || "").trim(),
        approved: row[table.index[CAMPAIGN_HEADERS.approved]],
        approvedAt: row[table.index[CAMPAIGN_HEADERS.approvedAt]],
        comment: row[table.index[CAMPAIGN_HEADERS.comment]],
      };
    });
    return campaigns;
  }

  function readStopEmails(sheet) {
    var lastRow = sheet.getLastRow();
    if (lastRow < 2) return {};
    var values = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
    return values.reduce(function (result, row) {
      var email = normalizeEmail(row[0]);
      if (email) result[email] = true;
      return result;
    }, {});
  }

  function loadContext() {
    var spreadsheet = getSpreadsheet();
    var queueSheet = requireSheet(spreadsheet, CONFIG.sheets.queue);
    var queue = readTable(queueSheet);
    requireHeaders(queue.index, QUEUE_HEADERS, CONFIG.sheets.queue);
    return {
      spreadsheet: spreadsheet,
      queueSheet: queueSheet,
      queue: queue,
      campaigns: readCampaigns(requireSheet(spreadsheet, CONFIG.sheets.campaigns)),
      template: readTemplate(requireSheet(spreadsheet, CONFIG.sheets.template)),
      stopEmails: readStopEmails(requireSheet(spreadsheet, CONFIG.sheets.stoplist)),
      eventsSheet: requireSheet(spreadsheet, CONFIG.sheets.events),
    };
  }

  function previewQueue() {
    var context = loadContext();
    var summary = { total: 0, eligibleForTestDraft: 0, blocked: {} };
    context.queue.rows.forEach(function (row) {
      if (!row.some(function (value) { return String(value || "").trim(); })) return;
      summary.total += 1;
      var candidate = candidateFromRow(row, context.queue.index);
      var reason = candidateEligibility(
        candidate,
        context.campaigns[candidate.campaignId],
        context.stopEmails,
        context.template
      );
      if (!reason) summary.eligibleForTestDraft += 1;
      else summary.blocked[reason] = (summary.blocked[reason] || 0) + 1;
    });
    Logger.log(JSON.stringify(summary));
    return summary;
  }

  function existingDraftEmails(rows, index) {
    return rows.reduce(function (result, row) {
      var candidate = candidateFromRow(row, index);
      if (candidate.email && candidate.draftId) result[candidate.email] = true;
      return result;
    }, {});
  }

  function previewWorkingQueue() {
    var context = loadContext();
    var draftedEmails = existingDraftEmails(context.queue.rows, context.queue.index);
    var summary = { total: 0, eligibleForWorkingDraft: 0, blocked: {} };
    context.queue.rows.forEach(function (row) {
      if (!row.some(function (value) { return String(value || "").trim(); })) return;
      summary.total += 1;
      var candidate = candidateFromRow(row, context.queue.index);
      var reason = workingDraftEligibility(
        candidate,
        context.campaigns[candidate.campaignId],
        context.stopEmails,
        context.template,
        draftedEmails
      );
      if (!reason) {
        summary.eligibleForWorkingDraft += 1;
        draftedEmails[candidate.email] = true;
      } else {
        summary.blocked[reason] = (summary.blocked[reason] || 0) + 1;
      }
    });
    Logger.log(JSON.stringify(summary));
    return summary;
  }

  function requireSenderConfiguration(properties) {
    var senderAlias = normalizeEmail(properties.getProperty(CONFIG.properties.senderAlias));
    if (!senderAlias) throw new Error("Не задан обязательный Gmail-алиас отправителя");
    return {
      senderAlias: senderAlias,
      senderName: String(properties.getProperty(CONFIG.properties.senderName) || "").trim(),
      replyTo: normalizeEmail(properties.getProperty(CONFIG.properties.replyTo)),
    };
  }

  function requireTestConfiguration() {
    var properties = PropertiesService.getScriptProperties();
    if (properties.getProperty(CONFIG.properties.testMode) !== "true") {
      throw new Error("TEST_MODE не включён явно в свойствах скрипта");
    }
    var testRecipient = normalizeEmail(
      properties.getProperty(CONFIG.properties.testRecipient)
    );
    if (!testRecipient) throw new Error("Не задан корректный тестовый адресат");
    var sender = requireSenderConfiguration(properties);
    sender.testRecipient = testRecipient;
    return sender;
  }

  function requireWorkingDraftConfiguration() {
    var properties = PropertiesService.getScriptProperties();
    if (properties.getProperty(CONFIG.properties.workingDraftsMode) !== "true") {
      throw new Error("WORKING_DRAFTS_MODE не включён явно в свойствах скрипта");
    }
    return requireSenderConfiguration(properties);
  }

  function appendEvent(
    sheet,
    campaignId,
    candidateId,
    email,
    type,
    oldStatus,
    newStatus,
    details
  ) {
    var eventId = Utilities.getUuid();
    sheet.appendRow(buildEventRow(
      new Date(),
      eventId,
      campaignId,
      candidateId,
      hashEmail(email),
      type,
      oldStatus,
      newStatus,
      Session.getEffectiveUser().getEmail(),
      details
    ));
  }

  function createTestDrafts() {
    var runtime = requireTestConfiguration();
    var lock = LockService.getScriptLock();
    lock.waitLock(5000);
    try {
      var context = loadContext();
      var created = [];
      for (var rowOffset = 0; rowOffset < context.queue.rows.length; rowOffset += 1) {
        var row = context.queue.rows[rowOffset];
        var candidate = candidateFromRow(row, context.queue.index);
        var campaign = context.campaigns[candidate.campaignId];
        var reason = candidateEligibility(
          candidate,
          campaign,
          context.stopEmails,
          context.template
        );
        if (reason) continue;

        var limit = Math.min(
          Math.max(Number(campaign.runLimit) || 0, 0),
          CONFIG.maxHardTestDraftsPerRun
        );
        if (!limit || created.length >= limit) continue;

        var sheetRow = rowOffset + 2;
        var statusColumn = context.queue.index[QUEUE_HEADERS.mailingStatus] + 1;
        var stageColumn = context.queue.index[QUEUE_HEADERS.stage] + 1;
        var draftColumn = context.queue.index[QUEUE_HEADERS.draftId] + 1;
        var noteColumn = context.queue.index[QUEUE_HEADERS.note] + 1;
        var oldStatus = candidate.mailingStatus;

        context.queueSheet
          .getRange(sheetRow, statusColumn)
          .setValue(CONFIG.queue.statusTestDraftCreating);
        SpreadsheetApp.flush();

        try {
          var subject = renderTemplate(context.template.subject, candidate);
          var body = renderTemplate(context.template.body, candidate);
          var testSubject = "[TEST → " + candidate.email + "] " + subject;
          var testBody =
            "ТЕСТОВЫЙ ЧЕРНОВИК. Получатель подменён на контрольный адрес.\n" +
            "Исходный адресат: " + candidate.email + "\n" +
            "ID кандидата: " + candidate.candidateId + "\n\n" +
            body;
          var options = { from: runtime.senderAlias };
          if (runtime.senderName) options.name = runtime.senderName;
          if (runtime.replyTo) options.replyTo = runtime.replyTo;
          var draft = createDraftViaGmailApi(
            runtime.testRecipient,
            testSubject,
            testBody,
            options
          );
          var draftId = String((draft && draft.id) || "");
          if (!draftId) {
            throw new Error("Gmail API не вернул ID созданного черновика");
          }
          context.queueSheet.getRange(sheetRow, draftColumn).setValue(draftId);
          context.queueSheet.getRange(sheetRow, statusColumn).setValue(CONFIG.queue.statusTestDraft);
          context.queueSheet.getRange(sheetRow, stageColumn).setValue("тест");
          context.queueSheet
            .getRange(sheetRow, noteColumn)
            .setValue("Тестовый адресат подменён; отправка запрещена");
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "test_draft_created",
            oldStatus,
            CONFIG.queue.statusTestDraft,
            "recipient_replaced_with_test_address"
          );
          created.push(candidate.candidateId);
        } catch (error) {
          context.queueSheet
            .getRange(sheetRow, statusColumn)
            .setValue(CONFIG.queue.statusTestDraftError);
          context.queueSheet.getRange(sheetRow, noteColumn).setValue(String(error.message || error));
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "test_draft_failed",
            CONFIG.queue.statusTestDraftCreating,
            CONFIG.queue.statusTestDraftError,
            String(error.message || error)
          );
        }
      }
      return { created: created.length, candidateIds: created };
    } finally {
      lock.releaseLock();
    }
  }

  function createWorkingDrafts() {
    var runtime = requireWorkingDraftConfiguration();
    var lock = LockService.getScriptLock();
    lock.waitLock(5000);
    try {
      var context = loadContext();
      var created = [];
      var createdByCampaign = {};
      var draftedEmails = existingDraftEmails(context.queue.rows, context.queue.index);

      for (var rowOffset = 0; rowOffset < context.queue.rows.length; rowOffset += 1) {
        if (created.length >= CONFIG.maxHardWorkingDraftsPerRun) break;

        var row = context.queue.rows[rowOffset];
        var candidate = candidateFromRow(row, context.queue.index);
        var campaign = context.campaigns[candidate.campaignId];
        var reason = workingDraftEligibility(
          candidate,
          campaign,
          context.stopEmails,
          context.template,
          draftedEmails
        );
        if (reason) continue;

        var campaignLimit = Math.min(
          Math.max(Number(campaign.runLimit) || 0, 0),
          CONFIG.maxHardWorkingDraftsPerRun
        );
        var campaignCreated = createdByCampaign[candidate.campaignId] || 0;
        if (!campaignLimit || campaignCreated >= campaignLimit) continue;

        var sheetRow = rowOffset + 2;
        var statusColumn = context.queue.index[QUEUE_HEADERS.mailingStatus] + 1;
        var stageColumn = context.queue.index[QUEUE_HEADERS.stage] + 1;
        var draftColumn = context.queue.index[QUEUE_HEADERS.draftId] + 1;
        var noteColumn = context.queue.index[QUEUE_HEADERS.note] + 1;
        var oldStatus = candidate.mailingStatus;

        context.queueSheet
          .getRange(sheetRow, statusColumn)
          .setValue(CONFIG.queue.statusWorkingDraftCreating);
        SpreadsheetApp.flush();
        draftedEmails[candidate.email] = true;

        try {
          var subject = renderTemplate(context.template.subject, candidate);
          var body = renderTemplate(context.template.body, candidate);
          var options = { from: runtime.senderAlias };
          if (runtime.senderName) options.name = runtime.senderName;
          if (runtime.replyTo) options.replyTo = runtime.replyTo;

          var draft = createDraftViaGmailApi(
            candidate.email,
            subject,
            body,
            options
          );
          var draftId = String((draft && draft.id) || "");
          if (!draftId) {
            throw new Error("Gmail API не вернул ID созданного рабочего черновика");
          }

          context.queueSheet.getRange(sheetRow, draftColumn).setValue(draftId);
          context.queueSheet
            .getRange(sheetRow, statusColumn)
            .setValue(CONFIG.queue.statusWorkingDraft);
          context.queueSheet
            .getRange(sheetRow, stageColumn)
            .setValue(CONFIG.queue.stageWorkingDraft);
          context.queueSheet
            .getRange(sheetRow, noteColumn)
            .setValue("Рабочий черновик создан; автоматическая отправка запрещена");
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "working_draft_created",
            oldStatus,
            CONFIG.queue.statusWorkingDraft,
            "draft_only_no_send"
          );
          created.push(candidate.candidateId);
          createdByCampaign[candidate.campaignId] = campaignCreated + 1;
        } catch (error) {
          context.queueSheet
            .getRange(sheetRow, statusColumn)
            .setValue(CONFIG.queue.statusWorkingDraftError);
          context.queueSheet.getRange(sheetRow, noteColumn).setValue(String(error.message || error));
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "working_draft_failed",
            CONFIG.queue.statusWorkingDraftCreating,
            CONFIG.queue.statusWorkingDraftError,
            String(error.message || error)
          );
        }
      }

      return {
        created: created.length,
        candidateIds: created,
        hardLimit: CONFIG.maxHardWorkingDraftsPerRun,
      };
    } finally {
      lock.releaseLock();
    }
  }

  function clampProductionBatchLimit(value) {
    var parsed = Math.floor(Number(value));
    if (!parsed || parsed < 1) parsed = CONFIG.defaultProductionBatchLimit;
    return Math.min(parsed, CONFIG.maxHardSendsPerRun);
  }

  function parseClockMinutes(value) {
    var match = String(value || "").match(/(\d{1,2}):(\d{2})/);
    if (!match) return null;
    var hours = Number(match[1]);
    var minutes = Number(match[2]);
    if (hours > 23 || minutes > 59) return null;
    return hours * 60 + minutes;
  }

  function isAllowedWeekday(days, weekday) {
    var label = normalizeLabel(days)
      .replace(/ё/g, "е")
      .replace(/[—−-]/g, "–");
    if (!label || label === "ежедневно" || label === "каждый день") return true;
    if (label === "пн–пт" || label === "будни" || label === "по будням") {
      return weekday >= 1 && weekday <= 5;
    }
    var names = { пн: 1, вт: 2, ср: 3, чт: 4, пт: 5, сб: 6, вс: 7 };
    var allowed = {};
    label.split(/[,;\s]+/).forEach(function (token) {
      if (names[token]) allowed[names[token]] = true;
    });
    return Boolean(allowed[weekday]);
  }

  function isScheduleOpen(campaign, weekday, minuteOfDay) {
    if (!isAllowedWeekday(campaign.days, weekday)) return false;
    var matches = String(campaign.sendWindow || "").match(/\d{1,2}:\d{2}/g) || [];
    if (matches.length < 2) return true;
    var start = parseClockMinutes(matches[0]);
    var end = parseClockMinutes(matches[1]);
    if (start === null || end === null) return false;
    if (start <= end) return minuteOfDay >= start && minuteOfDay <= end;
    return minuteOfDay >= start || minuteOfDay <= end;
  }

  function currentScheduleState(now, timeZone) {
    return {
      weekday: Number(Utilities.formatDate(now, timeZone, "u")),
      minuteOfDay:
        Number(Utilities.formatDate(now, timeZone, "HH")) * 60 +
        Number(Utilities.formatDate(now, timeZone, "mm")),
    };
  }

  function dateKey(value, timeZone) {
    if (!value) return "";
    var date = value instanceof Date ? value : new Date(value);
    if (isNaN(date.getTime())) return "";
    return Utilities.formatDate(date, timeZone, "yyyy-MM-dd");
  }

  function sentTodayByCampaign(rows, index, now, timeZone) {
    var today = dateKey(now, timeZone);
    return rows.reduce(function (result, row) {
      var candidate = candidateFromRow(row, index);
      if (
        candidate.campaignId &&
        candidate.messageId &&
        dateKey(candidate.sentAt, timeZone) === today
      ) {
        result[candidate.campaignId] = (result[candidate.campaignId] || 0) + 1;
      }
      return result;
    }, {});
  }

  function previewProductionQueue() {
    var context = loadContext();
    var summary = { total: 0, eligibleForSend: 0, blocked: {} };
    context.queue.rows.forEach(function (row) {
      if (!row.some(function (value) { return String(value || "").trim(); })) return;
      summary.total += 1;
      var candidate = candidateFromRow(row, context.queue.index);
      var reason = productionSendEligibility(
        candidate,
        context.campaigns[candidate.campaignId],
        context.stopEmails,
        context.template
      );
      if (!reason) summary.eligibleForSend += 1;
      else summary.blocked[reason] = (summary.blocked[reason] || 0) + 1;
    });
    Logger.log(JSON.stringify(summary));
    return summary;
  }

  function requireProductionConfiguration(properties) {
    if (properties.getProperty(CONFIG.properties.productionMode) !== "true") {
      throw new Error("PRODUCTION_MODE не включён явно в свойствах скрипта");
    }
    var sender = requireSenderConfiguration(properties);
    sender.batchLimit = clampProductionBatchLimit(
      properties.getProperty(CONFIG.properties.productionBatchLimit)
    );
    return sender;
  }

  function sendDraftViaGmailApi(draftId) {
    return Gmail.Users.Drafts.send({ id: String(draftId || "") }, "me");
  }

  function sendProductionBatch(scheduled) {
    var properties = PropertiesService.getScriptProperties();
    if (
      scheduled &&
      properties.getProperty(CONFIG.properties.schedulerMode) !== "true"
    ) {
      return { sent: 0, skipped: "scheduler_disabled" };
    }

    var runtime = requireProductionConfiguration(properties);
    var lock = LockService.getScriptLock();
    lock.waitLock(5000);
    try {
      var context = loadContext();
      var now = new Date();
      var timeZone =
        context.spreadsheet.getSpreadsheetTimeZone() || Session.getScriptTimeZone();
      var schedule = currentScheduleState(now, timeZone);
      var sentToday = sentTodayByCampaign(
        context.queue.rows,
        context.queue.index,
        now,
        timeZone
      );
      var sentThisRun = {};
      var sentCandidateIds = [];

      var statusColumn = context.queue.index[QUEUE_HEADERS.mailingStatus] + 1;
      var sentAtColumn = context.queue.index[QUEUE_HEADERS.sentAt] + 1;
      var stageColumn = context.queue.index[QUEUE_HEADERS.stage] + 1;
      var draftColumn = context.queue.index[QUEUE_HEADERS.draftId] + 1;
      var messageColumn = context.queue.index[QUEUE_HEADERS.messageId] + 1;
      var threadColumn = context.queue.index[QUEUE_HEADERS.threadId] + 1;
      var noteColumn = context.queue.index[QUEUE_HEADERS.note] + 1;
      if (
        sentAtColumn !== statusColumn + 1 ||
        stageColumn !== statusColumn + 2 ||
        draftColumn !== statusColumn + 3 ||
        messageColumn !== statusColumn + 4 ||
        threadColumn !== statusColumn + 5 ||
        noteColumn !== statusColumn + 6
      ) {
        throw new Error("Столбцы результата отправки имеют неожиданное расположение");
      }

      for (var rowOffset = 0; rowOffset < context.queue.rows.length; rowOffset += 1) {
        if (sentCandidateIds.length >= runtime.batchLimit) break;

        var row = context.queue.rows[rowOffset];
        var candidate = candidateFromRow(row, context.queue.index);
        var campaign = context.campaigns[candidate.campaignId];
        var reason = productionSendEligibility(
          candidate,
          campaign,
          context.stopEmails,
          context.template
        );
        if (reason) continue;
        if (
          scheduled &&
          !isScheduleOpen(campaign, schedule.weekday, schedule.minuteOfDay)
        ) {
          continue;
        }

        var runLimit = Math.min(
          Math.max(Number(campaign.runLimit) || 0, 0),
          runtime.batchLimit,
          CONFIG.maxHardSendsPerRun
        );
        var dailyLimit = Math.min(
          Math.max(Number(campaign.dailyLimit) || 0, 0),
          CONFIG.maxHardSendsPerDay
        );
        var campaignSentThisRun = sentThisRun[candidate.campaignId] || 0;
        var campaignSentToday = sentToday[candidate.campaignId] || 0;
        if (!runLimit || campaignSentThisRun >= runLimit) continue;
        if (!dailyLimit || campaignSentToday >= dailyLimit) continue;

        var sheetRow = rowOffset + 2;
        var oldStatus = candidate.mailingStatus;
        context.queueSheet
          .getRange(sheetRow, statusColumn)
          .setValue(CONFIG.queue.statusSending);
        SpreadsheetApp.flush();

        try {
          var sentMessage = sendDraftViaGmailApi(candidate.draftId);
          var messageId = String((sentMessage && sentMessage.id) || "");
          var threadId = String((sentMessage && sentMessage.threadId) || "");
          if (!messageId) throw new Error("Gmail API не вернул ID отправленного сообщения");

          context.queueSheet
            .getRange(sheetRow, statusColumn, 1, 7)
            .setValues([[
              CONFIG.queue.statusSent,
              now,
              CONFIG.queue.stageSent,
              candidate.draftId,
              messageId,
              threadId,
              "Отправлено через подтверждённую production-партию",
            ]]);
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "production_email_sent",
            oldStatus,
            CONFIG.queue.statusSent,
            "message_id=" + messageId + ";thread_id=" + threadId
          );
          sentCandidateIds.push(candidate.candidateId);
          sentThisRun[candidate.campaignId] = campaignSentThisRun + 1;
          sentToday[candidate.campaignId] = campaignSentToday + 1;
        } catch (error) {
          context.queueSheet
            .getRange(sheetRow, statusColumn)
            .setValue(CONFIG.queue.statusSendError);
          context.queueSheet
            .getRange(sheetRow, noteColumn)
            .setValue(String(error.message || error));
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "production_email_failed",
            CONFIG.queue.statusSending,
            CONFIG.queue.statusSendError,
            String(error.message || error)
          );
        }
      }

      return {
        sent: sentCandidateIds.length,
        candidateIds: sentCandidateIds,
        batchLimit: runtime.batchLimit,
        hardDailyLimit: CONFIG.maxHardSendsPerDay,
      };
    } finally {
      lock.releaseLock();
    }
  }

  function runProductionSchedule() {
    var result = sendProductionBatch(true);
    Logger.log(JSON.stringify(result));
    return result;
  }

  function onOpen() {
    SpreadsheetApp.getUi()
      .createMenu("Тендерная рассылка")
      .addItem("Проверить очередь (без почты)", "previewTenderOutreach")
      .addItem("Создать тестовые черновики", "createTenderTestDrafts")
      .addSeparator()
      .addItem("Проверить рабочие черновики", "previewTenderWorkingDrafts")
      .addItem("Создать рабочие черновики", "createTenderWorkingDrafts")
      .addSeparator()
      .addItem("Проверить production-партию", "previewTenderProductionBatch")
      .addItem("Отправить подтверждённую партию", "sendTenderProductionBatch")
      .addToUi();
  }

  return {
    CONFIG: CONFIG,
    normalizeEmail: normalizeEmail,
    asBoolean: asBoolean,
    indexHeaders: indexHeaders,
    isBlockedTemplate: isBlockedTemplate,
    renderTemplate: renderTemplate,
    plainTextToHtml: plainTextToHtml,
    sanitizeHeader: sanitizeHeader,
    buildEventRow: buildEventRow,
    validateCampaignForTest: validateCampaignForTest,
    validateCampaignForWorkingDrafts: validateCampaignForWorkingDrafts,
    validateCampaignForProduction: validateCampaignForProduction,
    candidateEligibility: candidateEligibility,
    workingDraftEligibility: workingDraftEligibility,
    productionSendEligibility: productionSendEligibility,
    clampProductionBatchLimit: clampProductionBatchLimit,
    isScheduleOpen: isScheduleOpen,
    previewQueue: previewQueue,
    previewWorkingQueue: previewWorkingQueue,
    previewProductionQueue: previewProductionQueue,
    createTestDrafts: createTestDrafts,
    createWorkingDrafts: createWorkingDrafts,
    sendProductionBatch: sendProductionBatch,
    runProductionSchedule: runProductionSchedule,
    onOpen: onOpen,
  };
})();

function onOpen() {
  return TenderOutreach.onOpen();
}

function previewTenderOutreach() {
  return TenderOutreach.previewQueue();
}

function createTenderTestDrafts() {
  return TenderOutreach.createTestDrafts();
}

function previewTenderWorkingDrafts() {
  return TenderOutreach.previewWorkingQueue();
}

function createTenderWorkingDrafts() {
  return TenderOutreach.createWorkingDrafts();
}

function previewTenderProductionBatch() {
  return TenderOutreach.previewProductionQueue();
}

function sendTenderProductionBatch() {
  return TenderOutreach.sendProductionBatch(false);
}

function runTenderProductionSchedule() {
  return TenderOutreach.runProductionSchedule();
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = TenderOutreach;
}
